from typing import List, Dict, Any, Optional

from engine.scanner import LivePatternScanner

class PatternDetector:
    @staticmethod
    def detect_patterns(candles: List[Dict[str, Any]], symbol: str = "TEST", interval: str = "1d") -> List[Dict[str, Any]]:
        scanner = LivePatternScanner(symbol=symbol, interval=interval)
        normalized = scanner._normalize_candles(candles)
        return [p.to_dict(normalized) for p in scanner.scan(candles)]

def calculate_avwap(candles: List[Dict[str, Any]], anchor_time: int) -> List[Dict[str, Any]]:
    avwap_data = []
    cumulative_pv = 0.0
    cumulative_vol = 0.0
    found_anchor = False
    for c in candles:
        if c['time'] >= anchor_time: found_anchor = True
        if found_anchor:
            tp = (c['high'] + c['low'] + c['close']) / 3.0
            cumulative_pv += tp * c['volume']
            cumulative_vol += c['volume']
            avwap_data.append({"time": c['time'], "value": float(cumulative_pv / cumulative_vol if cumulative_vol > 0 else tp)})
    return avwap_data
