import numpy as np
import pandas as pd
import xgboost as xgb
import joblib
import os
import json
import urllib.error
import urllib.parse
import urllib.request
import logging
from typing import List, Dict, Optional
from datetime import datetime

# Optional Anthropic import; Gemini uses REST so no Gemini SDK is required.
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

logger = logging.getLogger("future_app.ai")

EDUCATIONAL_AI_DISCLAIMER = (
    "Educational simulation only. This output is AI-generated commentary for learning, "
    "not investment advice, not a trade recommendation, and not SEBI-registered research."
)


class AIEngine:
    def __init__(self, model_path: str = "models/xgboost_quant_v1.joblib"):
        self.model_path = model_path
        self.model = self._load_model()
        self._llm_client = None
        self._llm_provider = None
        self._llm_model = None
        self._llm_available = False
        self._init_llm()

    def _init_llm(self):
        """Initialize Gemini first, then Claude, else use rule-based fallback."""
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            self._llm_client = gemini_key
            self._llm_provider = "gemini"
            self._llm_model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
            self._llm_available = True
            logger.info("Gemini commentary enabled (%s)", self._llm_model)
            return

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_key:
            logger.info("No Gemini/Anthropic API key set. Using rule-based educational fallback.")
            return

        if not ANTHROPIC_AVAILABLE:
            logger.info("anthropic package not installed. Using rule-based educational fallback.")
            return

        try:
            self._llm_client = anthropic.Anthropic(api_key=anthropic_key)
            self._llm_provider = "anthropic"
            self._llm_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            self._llm_available = True
            logger.info("Anthropic commentary enabled (%s)", self._llm_model)
        except Exception as e:
            logger.exception("Failed to initialize Anthropic client: %s", e)

    def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """Call the configured LLM provider. Returns response text or None on failure."""
        if not self._llm_available or not self._llm_client:
            return None
        if self._llm_provider == "gemini":
            return self._call_gemini(system_prompt, user_prompt, max_tokens)
        if self._llm_provider == "anthropic":
            return self._call_anthropic(system_prompt, user_prompt, max_tokens)
        return None

    def _call_anthropic(self, system_prompt: str, user_prompt: str, max_tokens: int) -> Optional[str]:
        try:
            message = self._llm_client.messages.create(
                model=self._llm_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return message.content[0].text
        except Exception as e:
            logger.warning("Anthropic call failed: %s", e)
            return None

    def _call_gemini(self, system_prompt: str, user_prompt: str, max_tokens: int) -> Optional[str]:
        try:
            model = urllib.parse.quote(self._llm_model, safe="")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self._llm_client}"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}],
                }],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": 0.2,
                },
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts).strip()
            return text or None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:500]
            logger.warning("Gemini call failed: HTTP %s %s", e.code, detail)
            return None
        except Exception as e:
            logger.warning("Gemini call failed: %s", e)
            return None

    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                return joblib.load(self.model_path)
            except Exception as e:
                logger.exception("Error loading model: %s", e)
        return None

    def engineer_features(self, candles: List[Dict], sr_levels: Dict) -> pd.DataFrame:
        """
        Principal Feature Engineering Pipeline.
        Converts raw OHLCV into educational quantitative features.
        """
        df = pd.DataFrame(candles)
        
        # 1. Trend Features
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        df['ema_slope_20'] = df['ema20'].diff(5) / df['ema20'].shift(5)
        df['ema_alignment'] = np.where((df['ema20'] > df['ema50']) & (df['ema50'] > df['ema200']), 1,
                                     np.where((df['ema20'] < df['ema50']) & (df['ema50'] < df['ema200']), -1, 0))

        # 2. Momentum Features
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        df['rsi_slope'] = df['rsi'].diff(3)
        
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_h'] = df['macd'] - df['macd'].ewm(span=9, adjust=False).mean()

        # 3. Volatility Features
        high_low = df['high'] - df['low']
        high_cp = np.abs(df['high'] - df['close'].shift())
        low_cp = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()
        df['atr_expansion'] = df['atr'] / df['atr'].rolling(window=50).mean()

        # 4. Volume Features
        avg_volume = df['volume'].rolling(window=20).mean()
        df['rel_vol'] = np.where(avg_volume > 0, df['volume'] / avg_volume, 1.0)
        df['vol_spike'] = np.where(df['rel_vol'] > 2.0, 1, 0)

        # 5. Support/Resistance Features (Confluence)
        current_price = df['close'].iloc[-1]
        supports = [l['price'] for l in sr_levels.get('support', sr_levels.get('supports', []))]
        resistances = [l['price'] for l in sr_levels.get('resistance', sr_levels.get('resistances', []))]
        
        dist_s = min([abs(current_price - s) for s in supports]) if supports else 999999
        dist_r = min([abs(current_price - r) for r in resistances]) if resistances else 999999
        
        df['dist_to_support'] = dist_s / current_price
        df['dist_to_resistance'] = dist_r / current_price

        # 6. Market Structure
        df['hh'] = np.where((df['high'] > df['high'].shift(1)) & (df['high'].shift(1) > df['high'].shift(2)), 1, 0)
        df['ll'] = np.where((df['low'] < df['low'].shift(1)) & (df['low'].shift(1) < df['low'].shift(2)), 1, 0)

        return df.replace([np.inf, -np.inf], 0).fillna(0)

    def predict(self, candles: List[Dict], sr_levels: Dict) -> Dict:
        """
        Generates educational model scores using the ensemble model.
        Returns probabilities and market regime analysis for simulation only.
        """
        if len(candles) < 200:
            return self._fallback_analysis(candles, sr_levels)
            
        features_df = self.engineer_features(candles, sr_levels)
        X = features_df.tail(1).drop(['time', 'open', 'high', 'low', 'close', 'volume', 'ema20', 'ema50', 'ema200'], axis=1, errors='ignore')
        
        # Real Model Prediction
        if self.model:
            try:
                probs = self.model.predict_proba(X)[0]
                return self._format_predictions(probs, features_df)
            except Exception as e:
                logger.exception("Model scoring error: %s", e)
                
        # Heuristic fallback if model not trained
        return self._heuristic_analysis(features_df, sr_levels)

    def _format_predictions(self, probs, df: pd.DataFrame) -> Dict:
        """Normalize model probabilities into the frontend model-lab response shape."""
        last = df.iloc[-1]
        bullish_prob = float(probs[-1]) if len(probs) else 0.5
        bullish_prob = max(0.05, min(0.95, bullish_prob))

        regime = "NEUTRAL"
        if last['ema_alignment'] == 1:
            regime = "TRENDING_BULLISH"
        elif last['ema_alignment'] == -1:
            regime = "TRENDING_BEARISH"
        elif last['atr_expansion'] < 0.8:
            regime = "ACCUMULATION_SIDEWAYS"

        support_hold = 0.5 + (0.25 * bullish_prob) if last['dist_to_support'] < 0.02 else 0.5
        resistance_break = bullish_prob if last['macd_h'] > 0 else bullish_prob * 0.75
        trend_continuation = 0.45 + (0.35 * bullish_prob) + (0.1 if last['ema_alignment'] != 0 else 0)
        trend_continuation = max(0.05, min(0.95, trend_continuation))

        return {
            "support_hold_probability": round(max(0.05, min(0.95, support_hold)), 2),
            "resistance_break_probability": round(max(0.05, min(0.95, resistance_break)), 2),
            "trend_continuation_probability": round(trend_continuation, 2),
            "reversal_probability": round(max(0.05, min(0.95, 1 - trend_continuation)), 2),
            "market_regime": regime,
            "confidence_score": round(max(0.5, min(0.95, abs(bullish_prob - 0.5) + 0.55)), 2),
            "ai_mode": "educational_model_demo",
            "validation_status": "not_independently_validated",
            "disclaimer": EDUCATIONAL_AI_DISCLAIMER,
        }

    def _heuristic_analysis(self, df: pd.DataFrame, sr: Dict) -> Dict:
        """Educational heuristic fallback used for cold-start or model failure."""
        last = df.iloc[-1]
        
        regime = "NEUTRAL"
        if last['ema_alignment'] == 1: regime = "TRENDING_BULLISH"
        elif last['ema_alignment'] == -1: regime = "TRENDING_BEARISH"
        elif last['atr_expansion'] < 0.8: regime = "ACCUMULATION_SIDEWAYS"
        
        trend_cont = 0.5 + (0.2 if last['ema_alignment'] != 0 else 0) + (0.1 if abs(last['rsi'] - 50) < 10 else 0)
        support_hold = 0.6 if last['dist_to_support'] < 0.01 else 0.5
        res_break = 0.4 if last['dist_to_resistance'] < 0.01 else 0.5
        
        if last['macd_h'] > 0 and last['rel_vol'] > 1.2:
            res_break += 0.15
            trend_cont += 0.1
            
        return {
            "support_hold_probability": round(min(0.95, support_hold), 2),
            "resistance_break_probability": round(min(0.95, res_break), 2),
            "trend_continuation_probability": round(min(0.95, trend_cont), 2),
            "reversal_probability": round(1 - trend_cont, 2),
            "market_regime": regime,
            "confidence_score": round(0.65 + (0.1 if last['rel_vol'] > 1 else 0), 2),
            "ai_mode": "educational_heuristic_demo",
            "validation_status": "not_independently_validated",
            "disclaimer": EDUCATIONAL_AI_DISCLAIMER,
        }

    def _fallback_analysis(self, candles: List[Dict], sr: Dict) -> Dict:
        return {
            "support_hold_probability": 0.5,
            "resistance_break_probability": 0.5,
            "trend_continuation_probability": 0.5,
            "reversal_probability": 0.5,
            "market_regime": "INSUFFICIENT_DATA",
            "confidence_score": 0.0,
            "ai_mode": "educational_fallback",
            "validation_status": "insufficient_data",
            "disclaimer": EDUCATIONAL_AI_DISCLAIMER,
        }

    # ─── AI-POWERED FUTURES ANALYSIS FUNCTIONS ──────────────────────────

    def analyze_trade_for_journal(self, position_data: Dict, trade_data: Dict, market_context: Dict) -> str:
        """
        AI-powered post-trade review for the trading journal.
        Uses Anthropic Claude when API key is configured, otherwise rule-based fallback.
        """
        entry_price = trade_data.get('entry_price', 0)
        exit_price = trade_data.get('exit_price', 0)
        pnl = trade_data.get('pnl', 0)
        side = trade_data.get('side', 'LONG')
        quantity = trade_data.get('quantity', 0)
        leverage = trade_data.get('leverage', 1)
        margin_mode = trade_data.get('margin_mode', 'ISOLATED')
        funding_rate = market_context.get('funding_rate', 0)
        mark_price = market_context.get('mark_price', 0)
        symbol = position_data.get('symbol', 'UNKNOWN')

        # Try LLM first
        system_prompt = (
            "You are an educational trading simulator coach, not a financial adviser. "
            "Review only the simulated trade process. Do not recommend real trades, "
            "promise outcomes, or imply investment advice. Be specific and data-driven. "
            "Format your response as 3-5 concise paragraphs covering execution process, "
            "leverage risk, funding cost impact, emotional bias, and practice points."
        )
        user_prompt = (
            f"Review this futures trade:\n"
            f"- Symbol: {symbol}\n"
            f"- Side: {side}\n"
            f"- Entry: ${entry_price:.2f}\n"
            f"- Exit: ${exit_price:.2f}\n"
            f"- Quantity: {quantity}\n"
            f"- Leverage: {leverage}x\n"
            f"- Margin Mode: {margin_mode}\n"
            f"- Realized PnL: ${pnl:.2f}\n"
            f"- Funding Rate at Entry: {funding_rate*100:.4f}%\n"
            f"- Mark Price at Review: ${mark_price:.2f}\n"
        )

        llm_response = self._call_llm(system_prompt, user_prompt, max_tokens=512)
        if llm_response:
            return llm_response

        # Rule-based fallback
        analysis = f"Educational trade review ({side}) - {symbol}:\n"
        if (side == 'LONG' and exit_price > entry_price) or (side == 'SHORT' and exit_price < entry_price):
            analysis += "- The simulated exit was favorable versus entry. Review whether the plan was defined before entry.\n"
        else:
            analysis += "- The simulated trade moved against entry. Review whether stop placement and invalidation rules were clear.\n"

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0
        if side == 'SHORT': pnl_pct = -pnl_pct
        analysis += f"- PnL: ${pnl:.2f} ({pnl_pct:.2f}% on {leverage}x leverage)\n"

        if side == 'LONG' and funding_rate > 0.001:
            analysis += "- Note: You paid significant funding fees on this long. Check funding schedule before entry.\n"
        elif side == 'SHORT' and funding_rate < -0.001:
            analysis += "- Note: Funding was favorable for this simulated short. Treat it as a cost component, not a timing signal.\n"

        if leverage > 50:
            analysis += "- WARNING: Leverage was extreme (>50x). This amplifies both gains and liquidation risk significantly.\n"
        elif leverage > 20:
            analysis += "- Leverage was moderate-high. Consider whether the volatility justified this level.\n"

        analysis += f"- {EDUCATIONAL_AI_DISCLAIMER}"
        return analysis

    def check_portfolio_risk(self, positions: List, wallet: Dict, correlations: Dict = None) -> str:
        """
        AI evaluation of overall portfolio risk.
        Uses Anthropic Claude when API key is configured, otherwise rule-based fallback.
        """
        total_notional = sum([abs(p.get('size', 0) * p.get('entry_price', 0)) for p in positions])
        equity = wallet.get('wallet_balance', 0) + wallet.get('unrealized_pnl', 0)

        if equity == 0:
            return "Portfolio is empty or liquidated."

        leverage = total_notional / equity

        # Try LLM first
        system_prompt = (
            "You are an educational simulator risk coach, not a financial adviser. "
            "Analyze simulated portfolio risk in 3-5 bullet points. Do not recommend "
            "real trades or imply guaranteed outcomes. Use severity labels: CRITICAL, "
            "WARNING, INFO."
        )
        user_prompt = (
            f"Portfolio Risk Assessment:\n"
            f"- Total Equity: ${equity:.2f}\n"
            f"- Total Notional Exposure: ${total_notional:.2f}\n"
            f"- Effective Leverage: {leverage:.2f}x\n"
            f"- Number of Positions: {len(positions)}\n"
            f"- Positions:\n"
        )
        for p in positions:
            notional = abs(p.get('size', 0) * p.get('entry_price', 0))
            user_prompt += (
                f"  - {p.get('symbol', '?')} {p.get('side', '?')} "
                f"Size: {p.get('size', 0)} @ ${p.get('entry_price', 0):.2f} "
                f"(Notional: ${notional:.2f})\n"
            )

        sides = [p.get('side') for p in positions]
        user_prompt += f"- Position Directions: {sides}\n"
        if correlations:
            user_prompt += f"- Asset Correlations: {json.dumps(correlations)}\n"

        llm_response = self._call_llm(system_prompt, user_prompt, max_tokens=512)
        if llm_response:
            return llm_response

        # Rule-based fallback
        risk_report = f"Portfolio Risk Assessment (Effective Leverage: {leverage:.2f}x):\n"

        if leverage > 20:
            risk_report += "CRITICAL: Extreme leverage detected. A 5% move will liquidate the account.\n"
        elif leverage > 10:
            risk_report += "WARNING: High leverage. Reduce exposure to avoid volatility spikes.\n"
        elif leverage > 5:
            risk_report += "INFO: Moderate leverage. Monitor positions during high-volatility events.\n"

        if len(positions) == 1:
            risk_report += "- High concentration: 100% of risk in a single asset.\n"

        sides = [p.get('side') for p in positions]
        if all(s == 'LONG' for s in sides) and len(sides) > 1:
            risk_report += "- One-sided exposure: Portfolio is purely long. No hedge against market drops."
        elif all(s == 'SHORT' for s in sides) and len(sides) > 1:
            risk_report += "- One-sided exposure: Portfolio is purely short. Vulnerable to short squeezes."

        return risk_report

    def get_market_summary(self, symbol: str, mark_price: float, funding_rate: float,
                            orderbook_depth: Dict, volatility_24h: float) -> str:
        """
        Generates a brief professional market summary.
        Uses Anthropic Claude when API key is configured, otherwise rule-based fallback.
        """
        # Try LLM first
        system_prompt = (
            "You are an educational market-structure explainer, not a financial adviser. "
            "Provide a brief 3-5 sentence simulator summary covering current price action, "
            "funding rate context, order book depth, and risk factors. Do not recommend trades."
        )
        bids = sum(orderbook_depth.get('bids_total', [0]))
        asks = sum(orderbook_depth.get('asks_total', [0]))
        user_prompt = (
            f"Market Summary for {symbol}:\n"
            f"- Mark Price: ${mark_price:.2f}\n"
            f"- Funding Rate (8h): {funding_rate*100:.4f}%\n"
            f"- 24h Volatility: {volatility_24h*100:.2f}%\n"
            f"- Order Book Bids Total: {bids}\n"
            f"- Order Book Asks Total: {asks}\n"
        )

        llm_response = self._call_llm(system_prompt, user_prompt, max_tokens=256)
        if llm_response:
            return llm_response

        # Rule-based fallback
        sentiment = "positive funding" if funding_rate > 0 else "negative funding"
        vol_desc = "high" if volatility_24h > 0.05 else "moderate"

        summary = f"{symbol} is currently trading at ${mark_price:.2f}. "
        summary += f"The funding context is {sentiment} at {funding_rate*100:.4f}%. "
        summary += f"Current 24h volatility is {vol_desc} ({volatility_24h*100:.2f}%). "

        if bids > asks * 1.5:
            summary += "The simulated order book currently shows larger bid depth."
        elif asks > bids * 1.5:
            summary += "The simulated order book currently shows larger ask depth."
        else:
            summary += "Order book is relatively balanced."

        return summary

    def advise_position_size(self, symbol: str, balance: float, leverage: int,
                              risk_pct: float, mark_price: float, volatility: float) -> Dict:
        """
        Calculate and explain an educational simulated position size.
        Uses Anthropic Claude when API key is configured, otherwise rule-based fallback.
        """
        # Risk-based sizing calculation (always compute for structured output)
        stop_dist = mark_price * volatility * 2
        if stop_dist == 0:
            stop_dist = mark_price * 0.05  # Fallback 5%

        max_loss = balance * (risk_pct / 100)
        example_size = max_loss / stop_dist

        # Cap by leverage
        max_leverage_size = (balance * leverage) / mark_price
        final_size = min(example_size, max_leverage_size)

        warnings = []
        if leverage > 20:
            warnings.append("Leverage is high (>20x), liquidation risk increases significantly")
        if volatility > 0.05:
            warnings.append("Volatility is elevated, consider reducing position size")
        if risk_pct > 2:
            warnings.append("Risk per trade exceeds 2% of balance — aggressive sizing")

        # Try LLM for enhanced reasoning
        system_prompt = (
            "You are an educational simulator risk coach. Given the practice-trading "
            "parameters, explain an example position size. Do not call it a recommendation "
            "for real trading and do not promise outcomes. Be concise."
        )
        user_prompt = (
            f"Position sizing for {symbol}:\n"
            f"- Account Balance: ${balance:.2f}\n"
            f"- Leverage: {leverage}x\n"
            f"- Risk per trade: {risk_pct}%\n"
            f"- Mark Price: ${mark_price:.2f}\n"
            f"- Volatility (24h): {volatility*100:.2f}%\n"
            f"- Calculated simulated example size: {final_size:.6f}\n"
            f"- Stop loss distance estimate: ${stop_dist:.2f}\n"
        )

        llm_response = self._call_llm(system_prompt, user_prompt, max_tokens=384)
        if llm_response:
            return {
                "recommended_size": round(final_size, 6),
                "example_size": round(final_size, 6),
                "reasoning": llm_response,
                "warnings": warnings,
                "disclaimer": EDUCATIONAL_AI_DISCLAIMER,
            }

        # Rule-based fallback
        return {
            "recommended_size": round(final_size, 6),
            "example_size": round(final_size, 6),
            "reasoning": f"Educational example using {risk_pct}% simulated risk and current {volatility*100:.1f}% volatility. "
                         f"Estimated stop distance is ${stop_dist:.2f}. "
                         f"Leverage-constrained maximum size is {max_leverage_size:.6f}.",
            "warnings": warnings,
            "disclaimer": EDUCATIONAL_AI_DISCLAIMER,
        }
