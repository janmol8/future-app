"""Spot paper-trading engine (single 'default' account, JSON persisted).

Rewritten to fix:
  * target exits were ignored whenever a stop-loss was set (elif bug)
  * market orders failed because the API dropped ``current_price``
  * prices never refreshed server-side, so SL/target/limit orders never fired
  * shallow-copied default account shared lists/dicts between accounts
  * reversing a position hard-coded account 'default' and checked cash before
    the closing proceeds were released
  * ``profit_factor`` was ``Infinity`` (invalid JSON for the browser)
  * no locking (Flask runs threaded) and non-atomic file writes
"""
import copy
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_trade_data.json')
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_log.txt')
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_BALANCE = 1000000
VALID_SIDES = ('BUY', 'SELL')
VALID_ORDER_TYPES = ('MARKET', 'LIMIT', 'STOP')
PRICE_REFRESH_MIN_SECONDS = 3.0


def _new_account(balance=DEFAULT_BALANCE):
    return {
        'balance': balance,
        'initial_balance': balance,
        'positions': {},
        'orders': [],
        'trade_history': [],
        'pnl_history': [],
        'created_at': int(time.time()),
    }


def log_trade(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _num(value):
    """float(value) or None for missing / non-finite / non-numeric input."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _default_price_fetcher(symbol):
    """Latest price for a symbol via the app's quote provider (cached)."""
    try:
        import stock_provider
        quote = stock_provider.get_live_quote(symbol, cache_ttl=3)
        price = _num((quote or {}).get('price'))
        return price if price and price > 0 else None
    except Exception:
        return None


class PaperTradeEngine:
    def __init__(self, data_file=None, price_fetcher=None):
        self.data_file = data_file or DATA_FILE
        self.price_fetcher = price_fetcher or _default_price_fetcher
        self._lock = threading.RLock()
        self._last_refresh = 0.0
        self.accounts = {'default': _new_account()}
        self.load()

    # ── persistence ────────────────────────────────────────────────
    def load(self):
        with self._lock:
            if not os.path.exists(self.data_file):
                return
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                acc = data.get('default')
                if isinstance(acc, dict):
                    merged = _new_account()
                    merged.update(acc)
                    for key in ('positions',):
                        if not isinstance(merged.get(key), dict):
                            merged[key] = {}
                    for key in ('orders', 'trade_history', 'pnl_history'):
                        if not isinstance(merged.get(key), list):
                            merged[key] = []
                    self.accounts['default'] = merged
            except Exception as e:
                print(f"Error loading paper trade data: {e}")

    def save(self):
        with self._lock:
            try:
                tmp = self.data_file + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(self.accounts, f, indent=2)
                os.replace(tmp, self.data_file)
            except Exception as e:
                print(f"Error saving paper trade data: {e}")

    # ── helpers ────────────────────────────────────────────────────
    @staticmethod
    def _position_pnl(pos, price):
        qty, avg = pos['quantity'], pos['avg_price']
        return (price - avg) * qty if pos['side'] == 'BUY' else (avg - price) * qty

    def _cash_needed(self, acc, symbol, side, qty, price):
        """Return (cash_to_commit, cash_released_by_closing) for an order."""
        pos = acc['positions'].get(symbol)
        if not pos or pos['side'] == side:
            return qty * price, 0.0
        close_qty = min(qty, pos['quantity'])
        released = pos['avg_price'] * close_qty + self._position_pnl({**pos, 'quantity': close_qty}, price)
        return max(qty - pos['quantity'], 0) * price, released

    def _price_for(self, symbol, supplied=None):
        price = _num(supplied)
        if price and price > 0:
            return price
        return self.fetch_price(symbol)

    def fetch_price(self, symbol):
        try:
            price = _num(self.price_fetcher(symbol))
        except Exception:
            price = None
        return price if price and price > 0 else None

    # ── account summary ────────────────────────────────────────────
    def get_account(self, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id) or _new_account()
            balance = acc['balance']
            initial = acc['initial_balance']
            invested = unrealized = 0.0
            for pos in acc['positions'].values():
                cur = pos.get('current_price') or pos['avg_price']
                invested += pos['quantity'] * pos['avg_price']
                unrealized += self._position_pnl(pos, cur)
            portfolio_value = balance + invested + unrealized
            total_pnl = portfolio_value - initial
            realized = sum(t.get('pnl', 0) for t in acc['trade_history'])
            today = datetime.now(IST).date()
            realized_today = sum(
                t.get('pnl', 0) for t in acc['trade_history']
                if datetime.fromtimestamp(t.get('exit_time', 0), IST).date() == today
            )
            return {
                'balance': balance,
                'initial_balance': initial,
                'portfolio_value': portfolio_value,
                'total_pnl': total_pnl,
                'total_pnl_pct': (total_pnl / initial * 100) if initial > 0 else 0,
                'invested_value': invested,
                'free_cash': balance,
                'unrealized_pnl': unrealized,
                'realized_pnl': realized,
                'realized_today': realized_today,
                'day_pnl': realized_today,
                'positions_count': len(acc['positions']),
                'pending_orders': sum(1 for o in acc['orders'] if o['status'] == 'PENDING'),
            }

    # ── orders ─────────────────────────────────────────────────────
    def place_order(self, symbol, order_type='MARKET', side='BUY', quantity=0, price=None,
                    trigger_price=None, stop_loss=None, target=None,
                    display_name='', current_price=None, account_id='default', stop_price=None, **_ignored):
        with self._lock:
            acc = self.accounts.get(account_id)
            if not acc:
                return self._fail('Account not found')

            symbol = str(symbol or '').strip().upper()
            side = str(side or '').strip().upper()
            order_type = str(order_type or 'MARKET').strip().upper()
            if not symbol:
                return self._fail('Symbol required')
            if side not in VALID_SIDES:
                return self._fail('Side must be BUY or SELL')
            if order_type not in VALID_ORDER_TYPES:
                return self._fail(f'Unsupported order type {order_type}')

            qty = _num(quantity)
            if qty is None or qty <= 0:
                return self._fail('Quantity must be greater than 0')

            limit_price = _num(price)
            trigger = _num(trigger_price if trigger_price is not None else stop_price)
            stop_loss, target = _num(stop_loss), _num(target)

            if order_type == 'LIMIT' and not (limit_price and limit_price > 0):
                return self._fail('Price required for LIMIT order')
            if order_type == 'STOP' and not (trigger and trigger > 0):
                return self._fail('Trigger price required for STOP order')

            live = self._price_for(symbol, current_price)
            if order_type == 'MARKET':
                if not live:
                    return self._fail(f'Live price unavailable for {symbol}. Try again in a moment.')
                est_price = live
            else:
                est_price = limit_price or trigger

            # Sanity-check protective levels against the entry price.
            if stop_loss and target and live:
                if side == 'BUY' and not (stop_loss < live < target):
                    return self._fail('For BUY: stop-loss must be below price and target above it')
                if side == 'SELL' and not (target < live < stop_loss):
                    return self._fail('For SELL: target must be below price and stop-loss above it')

            need, released = self._cash_needed(acc, symbol, side, qty, est_price)
            if acc['balance'] + released < need:
                return self._fail(
                    f"Insufficient balance. Need {need:,.2f}, available {acc['balance'] + released:,.2f}")

            now = int(time.time())
            order = {
                'order_id': f"ORD-{uuid.uuid4().hex[:8].upper()}",
                'symbol': symbol,
                'display_name': display_name or symbol,
                'order_type': order_type,
                'side': side,
                'quantity': qty,
                'price': limit_price,
                'trigger_price': trigger,
                'stop_loss': stop_loss,
                'target': target,
                'status': 'PENDING',
                'filled_price': None,
                'filled_at': None,
                'created_at': now,
                'notes': '',
            }
            acc['orders'].insert(0, order)
            del acc['orders'][500:]

            if order_type == 'MARKET':
                res = self._fill_order(acc, order, live, now)
                if not res.get('success'):
                    self.save()
                    return res
                message = f"{side} {qty:g} {symbol} @ {live:,.2f}"
            else:
                # An order that is already marketable fills immediately.
                if live and self._pending_should_fill(order, live):
                    self._fill_order(acc, order, live, now)
                    message = f"{order_type} {side} {qty:g} {symbol} filled @ {live:,.2f}"
                else:
                    ref = limit_price if order_type == 'LIMIT' else trigger
                    message = f"{order_type} {side} {qty:g} {symbol} @ {ref:,.2f} placed"
            self.save()
            return {'success': True, 'order_id': order['order_id'], 'message': message,
                    'filled_price': order['filled_price'], 'order': order}

    @staticmethod
    def _fail(message):
        return {'success': False, 'message': message, 'error': message}

    @staticmethod
    def _pending_should_fill(order, price):
        if order['order_type'] == 'LIMIT':
            lim = order['price']
            return price <= lim if order['side'] == 'BUY' else price >= lim
        if order['order_type'] == 'STOP':
            trg = order['trigger_price']
            return price >= trg if order['side'] == 'BUY' else price <= trg
        return False

    def _fill_order(self, acc, order, fill_price, now):
        sym, qty, side = order['symbol'], order['quantity'], order['side']
        need, released = self._cash_needed(acc, sym, side, qty, fill_price)
        if acc['balance'] + released < need:
            order['status'] = 'REJECTED'
            order['notes'] = 'Insufficient balance at fill time'
            return self._fail('Insufficient balance')

        order.update(status='FILLED', filled_price=fill_price, filled_at=now)
        log_trade(f"ORDER FILLED: {side} {qty:g} {sym} @ {fill_price}")

        pos = acc['positions'].get(sym)
        if pos and pos['side'] == side:
            total = pos['quantity'] + qty
            pos['avg_price'] = (pos['quantity'] * pos['avg_price'] + qty * fill_price) / total
            pos['quantity'] = total
            pos['current_price'] = fill_price
            pos['last_updated'] = now
            if order['stop_loss']:
                pos['stop_loss'] = order['stop_loss']
            if order['target']:
                pos['target'] = order['target']
            acc['balance'] -= qty * fill_price
        elif pos:
            close_qty = min(qty, pos['quantity'])
            self._close_locked(acc, sym, close_qty, fill_price, now)
            remaining = qty - close_qty
            if remaining > 1e-9:
                self._create_position(acc, order, remaining, fill_price, now)
        else:
            self._create_position(acc, order, qty, fill_price, now)
        return {'success': True}

    def _create_position(self, acc, order, qty, fill_price, now):
        log_trade(f"POSITION OPENED: {order['side']} {qty:g} {order['symbol']} @ {fill_price}")
        acc['positions'][order['symbol']] = {
            'symbol': order['symbol'],
            'display_name': order['display_name'],
            'side': order['side'],
            'quantity': qty,
            'avg_price': fill_price,
            'current_price': fill_price,
            'stop_loss': order['stop_loss'],
            'target': order['target'],
            'entry_time': now,
            'last_updated': now,
            'realized_pnl': 0,
        }
        acc['balance'] -= qty * fill_price

    def cancel_order(self, order_id, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id)
            if not acc:
                return self._fail('Account not found')
            for o in acc['orders']:
                if o['order_id'] == order_id and o['status'] == 'PENDING':
                    o['status'] = 'CANCELLED'
                    log_trade(f"ORDER CANCELLED: {order_id} ({o['symbol']})")
                    self.save()
                    return {'success': True, 'message': 'Order cancelled'}
            return self._fail('Order not found or not pending')

    # ── prices, auto exits, pending orders ─────────────────────────
    def refresh_prices(self, account_id='default', force=False):
        """Pull live prices for open positions / pending orders and run exits.
        Throttled; never raises (called from read endpoints)."""
        try:
            with self._lock:
                acc = self.accounts.get(account_id)
                if not acc:
                    return {}
                now = time.time()
                if not force and now - self._last_refresh < PRICE_REFRESH_MIN_SECONDS:
                    return {}
                symbols = set(acc['positions'])
                symbols.update(o['symbol'] for o in acc['orders'] if o['status'] == 'PENDING')
                if not symbols:
                    return {}
                self._last_refresh = now
                symbols = sorted(symbols)
            prices = {}
            for sym in symbols:  # network calls outside the lock
                p = self.fetch_price(sym)
                if p:
                    prices[sym] = p
            return self.update_prices(prices, account_id) if prices else {}
        except Exception as exc:
            print(f"paper refresh_prices error: {exc}")
            return {}

    def update_prices(self, price_map, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id)
            if not acc:
                return {}
            price_map = {str(k).strip().upper(): _num(v) for k, v in (price_map or {}).items()}
            now = int(time.time())
            sl_hits, target_hits, filled = [], [], []

            exits = []
            for sym, pos in acc['positions'].items():
                p = price_map.get(sym)
                if not p or p <= 0:
                    continue
                pos['current_price'] = p
                pos['last_updated'] = now
                is_buy = pos['side'] == 'BUY'
                sl, tgt = pos.get('stop_loss'), pos.get('target')
                if sl and ((is_buy and p <= sl) or (not is_buy and p >= sl)):
                    exits.append((sym, p, 'SL'))
                elif tgt and ((is_buy and p >= tgt) or (not is_buy and p <= tgt)):
                    exits.append((sym, p, 'TARGET'))

            for sym, p, reason in exits:
                trade = self._close_locked(acc, sym, None, p, now)
                pnl = trade['pnl'] if trade else 0
                log_trade(f"AUTO EXIT ({reason}): {sym} @ {p} | PnL: {pnl:+.2f}")
                (sl_hits if reason == 'SL' else target_hits).append({'symbol': sym, 'price': p, 'pnl': pnl})

            for o in acc['orders']:
                if o['status'] != 'PENDING':
                    continue
                p = price_map.get(o['symbol'])
                if p and p > 0 and self._pending_should_fill(o, p):
                    self._fill_order(acc, o, p, now)
                    filled.append(o)

            hist = acc['pnl_history']
            if not hist or now - hist[-1]['timestamp'] > 300:
                hist.append({'timestamp': now, 'portfolio_value': self.get_account(account_id)['portfolio_value']})
                del hist[:-500]

            self.save()
            return {'triggered_sl': sl_hits, 'triggered_target': target_hits, 'filled_orders': filled}

    # ── closing ────────────────────────────────────────────────────
    def _close_locked(self, acc, symbol, quantity, price, now=None):
        pos = acc['positions'].get(symbol)
        if not pos:
            return None
        qty = pos['quantity'] if not quantity else min(float(quantity), pos['quantity'])
        if qty <= 0:
            return None
        now = now or int(time.time())
        avg = pos['avg_price']
        pnl = self._position_pnl({**pos, 'quantity': qty}, price)
        acc['balance'] += avg * qty + pnl
        trade = {
            'symbol': symbol,
            'display_name': pos.get('display_name', symbol),
            'side': pos['side'],
            'quantity': qty,
            'entry_price': avg,
            'exit_price': price,
            'pnl': pnl,
            'pnl_pct': (pnl / (avg * qty) * 100) if avg > 0 else 0,
            'entry_time': pos['entry_time'],
            'exit_time': now,
            'duration_mins': (now - pos['entry_time']) / 60,
        }
        acc['trade_history'].insert(0, trade)
        del acc['trade_history'][2000:]
        log_trade(f"POSITION CLOSED: {symbol} x{qty:g} @ {price} | PnL: {pnl:+.2f}")
        pos['quantity'] -= qty
        if pos['quantity'] <= 1e-9:
            del acc['positions'][symbol]
        return trade

    def close_position(self, symbol, quantity=None, current_price=None, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id)
            symbol = str(symbol or '').strip().upper()
            if not acc or symbol not in acc['positions']:
                return self._fail('Position not found')
            pos = acc['positions'][symbol]
            qty = _num(quantity)
            if quantity not in (None, '') and (qty is None or qty <= 0):
                return self._fail('Invalid quantity')
            price = self._price_for(symbol, current_price) or pos.get('current_price')
            if not price:
                return self._fail(f'Live price unavailable for {symbol}')
            trade = self._close_locked(acc, symbol, qty, price)
            if not trade:
                return self._fail('Nothing to close')
            self.save()
            return {'success': True, 'realized_pnl': trade['pnl'], 'trade': trade}

    # ── read models ────────────────────────────────────────────────
    def get_positions_with_pnl(self, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id)
            if not acc:
                return []
            res = []
            for pos in acc['positions'].values():
                p = dict(pos)
                cur = p.get('current_price') or p['avg_price']
                pnl = self._position_pnl(p, cur)
                invested = p['quantity'] * p['avg_price']
                p.update(
                    current_price=cur,
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=(pnl / invested * 100) if invested > 0 else 0,
                    current_value=invested + pnl,
                    invested_value=invested,
                )
                res.append(p)
            return sorted(res, key=lambda x: x['entry_time'], reverse=True)

    def get_orders(self, account_id='default'):
        with self._lock:
            return list(self.accounts.get(account_id, {}).get('orders', []))

    def get_history(self, account_id='default'):
        with self._lock:
            return list(self.accounts.get(account_id, {}).get('trade_history', []))

    def get_pnl_history(self, account_id='default'):
        with self._lock:
            return list(self.accounts.get(account_id, {}).get('pnl_history', []))

    def get_performance_stats(self, account_id='default'):
        with self._lock:
            acc = self.accounts.get(account_id)
            history = list(acc['trade_history']) if acc else []
        total = len(history)
        if total == 0:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0, 'win_rate': 0,
                'avg_profit_pct': 0, 'avg_loss_pct': 0, 'profit_factor': None,
                'best_trade': None, 'worst_trade': None, 'total_realized_pnl': 0,
                'current_streak': 0, 'avg_holding_duration': 0,
            }
        wins = [t for t in history if t['pnl'] > 0]
        losses = [t for t in history if t['pnl'] <= 0]
        total_profit = sum(t['pnl'] for t in wins)
        total_loss = abs(sum(t['pnl'] for t in losses))
        best = max(history, key=lambda x: x['pnl'])
        worst = min(history, key=lambda x: x['pnl'])
        is_win = history[0]['pnl'] > 0
        streak = 0
        for t in history:
            if (t['pnl'] > 0) == is_win:
                streak += 1
            else:
                break
        return {
            'total_trades': total,
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'win_rate': len(wins) / total * 100,
            'avg_profit_pct': sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0,
            'avg_loss_pct': sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0,
            # None (not Infinity, which is invalid JSON) when there are no losses.
            'profit_factor': (total_profit / total_loss) if total_loss > 0 else None,
            'best_trade': {'symbol': best['symbol'], 'pnl': best['pnl'], 'pnl_pct': best['pnl_pct']},
            'worst_trade': {'symbol': worst['symbol'], 'pnl': worst['pnl'], 'pnl_pct': worst['pnl_pct']},
            'total_realized_pnl': sum(t['pnl'] for t in history),
            'current_streak': streak if is_win else -streak,
            'avg_holding_duration': sum(t['duration_mins'] for t in history) / total,
        }

    def reset_account(self, starting_balance=DEFAULT_BALANCE, account_id='default'):
        with self._lock:
            bal = _num(starting_balance)
            if not bal or bal <= 0:
                bal = DEFAULT_BALANCE
            self.accounts[account_id] = _new_account(bal)
            log_trade(f"ACCOUNT RESET: New balance {bal}")
            self.save()


engine = PaperTradeEngine()
