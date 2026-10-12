from __future__ import annotations

from typing import List

import numpy as np

from .primitives import Pivot


class TrendlineEngine:
    @staticmethod
    def fit_ols(points: List[dict]) -> dict:
        if not points:
            return {"slope": 0.0, "intercept": 0.0, "r2": 0.0}
        if len(points) < 2:
            return {"slope": 0.0, "intercept": float(points[0]["price"]), "r2": 1.0}
        xs = np.array([p["index"] for p in points], dtype=float)
        ys = np.array([p["price"] for p in points], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        pred = slope * xs + intercept
        ss_res = float(np.sum((ys - pred) ** 2))
        ss_tot = float(np.sum((ys - ys.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
        return {"slope": float(slope), "intercept": float(intercept), "r2": float(max(0.0, min(1.0, r2)))}

    @staticmethod
    def fit_theil_sen(pivots: List[Pivot]) -> dict:
        slopes = []
        for i in range(len(pivots)):
            for j in range(i + 1, len(pivots)):
                dx = pivots[j].index - pivots[i].index
                if dx:
                    slopes.append((pivots[j].price - pivots[i].price) / dx)
        slopes.sort()
        slope = slopes[len(slopes) // 2] if slopes else 0.0
        intercepts = sorted([p.price - slope * p.index for p in pivots])
        intercept = intercepts[len(intercepts) // 2] if intercepts else 0.0
        return {"slope": float(slope), "intercept": float(intercept), "r2": 0.0}

    @staticmethod
    def price_at(line: dict, index: int) -> float:
        return float(line.get("slope", 0.0) * index + line.get("intercept", 0.0))

    @staticmethod
    def slope_degrees(slope: float, scale: float = 1.0) -> float:
        return abs(float(np.degrees(np.arctan(slope / max(scale, 1e-9)))))

