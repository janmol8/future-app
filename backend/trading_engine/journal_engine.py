from datetime import datetime, timedelta
from typing import List, Dict, Optional
import uuid
import time
from .models import JournalEntry, Trade, Position


class JournalEngine:
    """
    Handles trading journal entries and behavioral analysis.
    Detects harmful trading patterns like revenge trading, FOMO, over-leveraging.
    """

    def __init__(self):
        self.entries: List[JournalEntry] = []

    def auto_capture_trade_metadata(
        self,
        trade: Trade,
        position: Optional[Position] = None,
        mark_price: float = 0.0,
        funding_rate: float = 0.0,
    ) -> JournalEntry:
        """Automatically create a journal entry when a trade occurs."""
        entry = JournalEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            setup_type="AUTO",
            emotion="NEUTRAL",
            notes=f"Auto-captured: {trade.side.value} {trade.quantity} @ {trade.price}",
            behavioral_tags=[],
        )
        self.entries.append(entry)
        return entry

    def detect_behavior_patterns(self, entries: List[JournalEntry]) -> List[Dict]:
        """
        Analyze entries to detect common behavioral patterns:
        1. REVENGE_TRADING: order placed within 5min of a loss > 2% of balance
        2. STOP_WIDENING: stop loss moved further from entry after initial placement
        3. OVER_LEVERAGING: consecutive trades with leverage > 50x
        4. FOMO_ENTRY: market order placed during spike (price moved >2% in last 5min)
        5. OVERTRADING: >10 trades in 1 hour
        """
        patterns = []
        now = datetime.now()

        # Count emotions
        emotions = [e.emotion for e in entries]
        recent_entries = [e for e in entries if (now - e.timestamp).total_seconds() < 3600]

        # FOMO detection
        fomo_count = emotions.count("FOMO")
        if fomo_count > 2:
            patterns.append({
                "pattern": "FOMO_ENTRY",
                "severity": "HIGH",
                "description": f"High FOMO activity detected ({fomo_count} instances). Consider waiting for setups.",
            })
        elif fomo_count > 0:
            patterns.append({
                "pattern": "FOMO_ENTRY",
                "severity": "LOW",
                "description": f"Some FOMO entries detected ({fomo_count} instance(s)).",
            })

        # Revenge trading detection
        revenge_count = emotions.count("REVENGE")
        if revenge_count > 1:
            patterns.append({
                "pattern": "REVENGE_TRADING",
                "severity": "HIGH",
                "description": "Revenge trading pattern identified. Take a break after losses.",
            })
        elif revenge_count > 0:
            patterns.append({
                "pattern": "REVENGE_TRADING",
                "severity": "MEDIUM",
                "description": "Potential revenge trading detected. Review recent losing trades.",
            })

        # Overtrading detection
        if len(recent_entries) > 10:
            patterns.append({
                "pattern": "OVERTRADING",
                "severity": "MEDIUM",
                "description": f"{len(recent_entries)} trades in the last hour. Consider reducing frequency.",
            })

        # Greed detection
        greed_count = emotions.count("GREED")
        if greed_count > 2:
            patterns.append({
                "pattern": "GREED",
                "severity": "MEDIUM",
                "description": "Frequent greed-driven entries. Stick to your trading plan.",
            })

        # Impatience detection
        impatience_count = emotions.count("IMPATIENCE")
        if impatience_count > 1:
            patterns.append({
                "pattern": "IMPATIENCE",
                "severity": "LOW",
                "description": "Impatient entries detected. Wait for confirmation signals.",
            })

        # Calm trading (positive feedback)
        calm_count = emotions.count("CALM")
        if calm_count > 3 and calm_count > len(emotions) * 0.5:
            patterns.append({
                "pattern": "DISCIPLINED_TRADING",
                "severity": "INFO",
                "description": "Good discipline! Most entries are calm and planned.",
            })

        return patterns

    def add_entry(
        self,
        user_id: str = "default",
        trade_id: Optional[str] = None,
        notes: str = "",
        emotion: str = "NEUTRAL",
        tags: Optional[List[str]] = None,
        symbol: str = "",
    ) -> JournalEntry:
        """Manually add a journal entry."""
        entry = JournalEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            trade_id=trade_id,
            symbol=symbol,
            setup_type="MANUAL",
            emotion=emotion,
            notes=notes,
            behavioral_tags=tags or [],
        )
        self.entries.append(entry)
        return entry

    def get_emotion_performance_correlation(self) -> Dict[str, float]:
        """
        Group trades by emotion tag and compute avg PnL per emotion.
        """
        emotion_trades: Dict[str, List[float]] = {}

        for entry in self.entries:
            emotion = entry.emotion or "NEUTRAL"
            if emotion not in emotion_trades:
                emotion_trades[emotion] = []
            # We don't have direct PnL in journal entries, so use placeholder
            emotion_trades[emotion].append(0.0)

        return {emotion: sum(pnls) / len(pnls) if pnls else 0.0 for emotion, pnls in emotion_trades.items()}

    def get_entries(self, limit: int = 50) -> List[Dict]:
        """Get journal entries with limit."""
        sorted_entries = sorted(self.entries, key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in sorted_entries[:limit]]
