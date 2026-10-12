class ConfidenceEngine:
    @staticmethod
    def score(shape_fit, trend_context, volume_context, breakout_quality, retest_quality, clarity, uniformity, mtf_alignment, pre_breakout=False) -> int:
        if pre_breakout:
            raw = 0.40 * shape_fit + 0.20 * trend_context + 0.15 * volume_context + 0.15 * clarity + 0.10 * uniformity
        else:
            raw = (
                0.30 * shape_fit + 0.15 * trend_context + 0.10 * volume_context +
                0.15 * breakout_quality + 0.10 * retest_quality + 0.10 * clarity +
                0.05 * uniformity + 0.05 * mtf_alignment
            )
        return max(0, min(100, round(raw * 100)))

