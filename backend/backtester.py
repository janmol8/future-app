
import pandas as pd
import numpy as np

class Backtester:
    def __init__(self, candles, initial_capital=100000):
        self.df = pd.DataFrame(candles)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position = 0
        self.trades = []
        self.equity_curve = []

    def run(self, strategy_config):
        """
        strategy_config: {
            "entry_conditions": [{"indicator": "sma", "period": 20, "operator": ">", "value": "close"}],
            "exit_conditions": [{"indicator": "sma", "period": 20, "operator": "<", "value": "close"}],
            "stop_loss": 2.0,  # percent
            "take_profit": 5.0 # percent
        }
        """
        df = self.df.copy()
        
        # Pre-calculate indicators
        all_indicators = {}
        conditions = strategy_config.get("entry_conditions", []) + strategy_config.get("exit_conditions", [])
        
        for cond in conditions:
            key = f"{cond['indicator']}_{cond['period']}"
            if key not in all_indicators:
                if cond['indicator'] == 'sma':
                    all_indicators[key] = df['close'].rolling(window=int(cond['period'])).mean()
                elif cond['indicator'] == 'ema':
                    all_indicators[key] = df['close'].ewm(span=int(cond['period']), adjust=False).mean()
                elif cond['indicator'] == 'rsi':
                    delta = df['close'].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=int(cond['period'])).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=int(cond['period'])).mean()
                    rs = gain / loss
                    all_indicators[key] = 100 - (100 / (1 + rs))

        for key, values in all_indicators.items():
            df[key] = values

        # Bar-by-bar evaluation
        for i in range(len(df)):
            row = df.iloc[i]
            price = row['close']
            timestamp = row['time']

            if self.position == 0:
                if self._check_conditions(row, strategy_config['entry_conditions']):
                    self.position = self.capital / price
                    entry_price = price
                    self.trades.append({
                        "type": "BUY",
                        "entry_time": timestamp,
                        "entry_price": entry_price,
                        "size": self.position
                    })
            else:
                current_trade = self.trades[-1]
                pnl_pct = (price - current_trade['entry_price']) / current_trade['entry_price'] * 100
                
                exit_signal = self._check_conditions(row, strategy_config['exit_conditions'])
                stop_loss_hit = strategy_config.get('stop_loss') and pnl_pct <= -strategy_config['stop_loss']
                take_profit_hit = strategy_config.get('take_profit') and pnl_pct >= strategy_config['take_profit']

                if exit_signal or stop_loss_hit or take_profit_hit:
                    self.capital = self.position * price
                    current_trade.update({
                        "exit_time": timestamp,
                        "exit_price": price,
                        "pnl": self.capital - (current_trade['size'] * current_trade['entry_price']),
                        "pnl_pct": pnl_pct,
                        "exit_reason": "SIGNAL" if exit_signal else ("STOP_LOSS" if stop_loss_hit else "TAKE_PROFIT")
                    })
                    self.position = 0

            current_equity = self.capital if self.position == 0 else self.position * price
            self.equity_curve.append({"time": timestamp, "value": current_equity})

        return self._format_results()

    def _check_conditions(self, row, conditions):
        if not conditions: return False
        
        results = []
        for cond in conditions:
            val1 = row[f"{cond['indicator']}_{cond['period']}"] if cond['indicator'] != 'price' else row['close']
            
            if isinstance(cond['value'], str) and cond['value'] == 'close':
                val2 = row['close']
            else:
                try: val2 = float(cond['value'])
                except: val2 = 0
            
            op = cond['operator']
            if op == ">": results.append(val1 > val2)
            elif op == "<": results.append(val1 < val2)
            elif op == "crosses_above": 
                # Simplified for bar-by-bar; ideally needs prev row
                results.append(val1 > val2)
            elif op == "crosses_below":
                results.append(val1 < val2)
        
        return all(results) if results else False

    def _format_results(self):
        closed_trades = [t for t in self.trades if 'exit_price' in t]
        if not closed_trades:
            return {
                "summary": {"total_trades": 0, "win_rate": 0, "net_profit": 0, "profit_factor": 0},
                "trades": [],
                "equity_curve": self.equity_curve
            }

        wins = [t for t in closed_trades if t['pnl'] > 0]
        losses = [t for t in closed_trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in closed_trades)
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        
        return {
            "summary": {
                "total_trades": len(closed_trades),
                "win_rate": round(len(wins) / len(closed_trades) * 100, 2),
                "net_profit": round(total_pnl, 2),
                "net_profit_pct": round((total_pnl / self.initial_capital) * 100, 2),
                "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
                "max_drawdown": self._calculate_drawdown()
            },
            "trades": closed_trades,
            "equity_curve": self.equity_curve
        }

    def _calculate_drawdown(self):
        values = [e['value'] for e in self.equity_curve]
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak: peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd: max_dd = dd
        return round(max_dd, 2)
