import numpy as np
from typing import List, Dict
from decimal import Decimal
from .models import Trade


class AnalyticsEngine:
    """
    Calculates portfolio performance metrics and generates reports.
    Uses numpy for statistical calculations.
    """

    @staticmethod
    def calculate_win_rate(trades: List[Trade]) -> float:
        """Win Rate = Winning Trades / Total Trades × 100"""
        if not trades:
            return 0.0
        wins = [t for t in trades if t.realized_pnl > 0]
        return len(wins) / len(trades) * 100

    @staticmethod
    def calculate_profit_factor(pnl_list: List[float]) -> float:
        """Profit Factor = Σ Winning PnL / |Σ Losing PnL|"""
        gross_profits = sum(p for p in pnl_list if p > 0)
        gross_losses = abs(sum(p for p in pnl_list if p < 0))
        if gross_losses == 0:
            return float('inf') if gross_profits > 0 else 0.0
        return gross_profits / gross_losses

    @staticmethod
    def calculate_sharpe_ratio(daily_returns: List[float], risk_free_rate: float = 0.05) -> float:
        """
        Sharpe = (Annualized Return - Risk Free Rate) / (StdDev of Daily Returns × √252)
        Returns 0.0 if insufficient data (<7 data points).
        """
        if len(daily_returns) < 7:
            return 0.0
        returns_array = np.array(daily_returns)
        mean_return = np.mean(returns_array)
        std_return = np.std(returns_array, ddof=1)
        if std_return == 0:
            return 0.0
        return float((mean_return - risk_free_rate) / (std_return * np.sqrt(252)))

    @staticmethod
    def calculate_sortino_ratio(daily_returns: List[float], risk_free_rate: float = 0.05) -> float:
        """
        Sortino = (Annualized Return - Risk Free Rate) / Downside Deviation
        Downside Deviation = sqrt(mean of squared negative returns only) × √252
        """
        if len(daily_returns) < 7:
            return 0.0
        returns_array = np.array(daily_returns)
        mean_return = np.mean(returns_array)
        downside_returns = returns_array[returns_array < 0]
        if len(downside_returns) < 2:
            return 0.0
        downside_std = np.std(downside_returns, ddof=1)
        if downside_std == 0:
            return 0.0
        return float((mean_return - risk_free_rate) / (downside_std * np.sqrt(252)))

    @staticmethod
    def calculate_calmar_ratio(equity_curve: List[float]) -> float:
        """Calmar = CAGR / |Max Drawdown|"""
        if len(equity_curve) < 2:
            return 0.0
        initial = equity_curve[0]
        final = equity_curve[-1]
        if initial <= 0:
            return 0.0

        max_dd = AnalyticsEngine.calculate_max_drawdown(equity_curve)
        if max_dd == 0:
            return 0.0

        days = len(equity_curve)
        if days <= 0:
            return 0.0
        cagr = (final / initial) ** (365.0 / days) - 1
        return float(cagr / abs(max_dd))

    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> float:
        """
        MaxDD_t = max over τ≤t of (Equity_τ - Equity_t) / Equity_τ
        Returns as percentage (e.g. 0.23 = 23% drawdown)
        """
        if not equity_curve:
            return 0.0
        arr = np.array(equity_curve)
        peak = np.maximum.accumulate(arr)
        drawdown = (arr - peak) / np.where(peak != 0, peak, 1)
        return float(np.min(drawdown))

    @staticmethod
    def calculate_expectancy(trades: List[Trade]) -> float:
        """Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)"""
        if not trades:
            return 0.0
        wins = [t.realized_pnl for t in trades if t.realized_pnl > 0]
        losses = [abs(t.realized_pnl) for t in trades if t.realized_pnl < 0]

        win_rate = len(wins) / len(trades)
        loss_rate = len(losses) / len(trades)
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        return (win_rate * avg_win) - (loss_rate * avg_loss)

    @staticmethod
    def calculate_average_rr(trades: List[Trade]) -> float:
        """Average R:R = Avg Profit of Wins / Avg Loss of Losses"""
        wins = [float(t.realized_pnl) for t in trades if t.realized_pnl > 0]
        losses = [abs(float(t.realized_pnl)) for t in trades if t.realized_pnl < 0]

        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        if avg_loss == 0:
            return 0.0
        return avg_win / avg_loss

    @staticmethod
    def calculate_consecutive_runs(trades: List[Trade]) -> Dict:
        """Returns: {max_consecutive_wins, max_consecutive_losses, current_streak}"""
        if not trades:
            return {"max_consecutive_wins": 0, "max_consecutive_losses": 0, "current_streak": 0}

        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        streak = 0

        for t in trades:
            if t.realized_pnl > 0:
                current_wins += 1
                current_losses = 0
                streak = current_wins
            else:
                current_losses += 1
                current_wins = 0
                streak = -current_losses

            max_wins = max(max_wins, current_wins)
            max_losses = max(max_losses, current_losses)

        return {
            "max_consecutive_wins": max_wins,
            "max_consecutive_losses": max_losses,
            "current_streak": streak,
        }

    def generate_full_report(
        self,
        trades: List[Trade],
        pnl_list: List[float],
        equity_curve: List[float]
    ) -> Dict:
        """Returns complete analytics dict with all metrics."""
        win_rate = self.calculate_win_rate(trades)
        profit_factor = self.calculate_profit_factor(pnl_list)
        sharpe = self.calculate_sharpe_ratio(pnl_list)
        sortino = self.calculate_sortino_ratio(pnl_list)
        calmar = self.calculate_calmar_ratio(equity_curve)
        max_dd = self.calculate_max_drawdown(equity_curve)
        expectancy = self.calculate_expectancy(trades)
        avg_rr = self.calculate_average_rr(trades)
        consecutive = self.calculate_consecutive_runs(trades)

        total_fees = sum(float(t.fee) for t in trades)
        total_funding = sum(float(t.funding_paid) for t in trades if t.funding_paid)
        net_profit = sum(pnl_list)

        best_trade = None
        worst_trade = None
        if trades:
            best = max(trades, key=lambda t: t.realized_pnl)
            worst = min(trades, key=lambda t: t.realized_pnl)
            best_trade = {
                "symbol": best.symbol,
                "pnl": str(best.realized_pnl),
                "side": best.side.value,
            }
            worst_trade = {
                "symbol": worst.symbol,
                "pnl": str(worst.realized_pnl),
                "side": worst.side.value,
            }

        return {
            "metrics": {
                "win_rate": win_rate / 100 if win_rate else 0,
                "profit_factor": profit_factor if profit_factor != float('inf') else 99.99,
                "sharpe": sharpe,
                "sortino": sortino,
                "calmar": calmar,
                "max_dd": abs(max_dd),
                "avg_rr": avg_rr,
                "expectancy": expectancy,
                "net_profit": net_profit,
            },
            "summary": {
                "total_trades": len(trades),
                "winning_trades": len([t for t in trades if t.realized_pnl > 0]),
                "losing_trades": len([t for t in trades if t.realized_pnl < 0]),
                "total_fees": total_fees,
                "total_funding": total_funding,
                "roi_pct": float((equity_curve[-1] / equity_curve[0] - 1) * 100) if equity_curve and equity_curve[0] > 0 else 0,
            },
            "consecutive": consecutive,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "equity_curve": [{"balance": e} for e in equity_curve] if equity_curve else [],
            "distribution": {
                "wins": len([t for t in trades if t.realized_pnl > 0]),
                "losses": len([t for t in trades if t.realized_pnl <= 0]),
            },
            "emotion_data": {},
        }
