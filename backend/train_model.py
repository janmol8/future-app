import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
from typing import List, Dict
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from ai_engine import AIEngine
import stock_provider
import crypto_provider

class ModelTrainer:
    def __init__(self, ai_engine: AIEngine):
        self.ai_engine = ai_engine
        self.models_dir = "models"
        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)

    def prepare_training_data(self, symbols: List[str], interval: str = "1h", days: int = 365):
        """
        Fetches historical data for multiple symbols and creates a labeled dataset.
        Target: Bullish movement of > 2% within next 10 candles.
        """
        all_features = []
        all_labels = []

        print(f"[Trainer] Preparing data for {len(symbols)} symbols...")

        for symbol in symbols:
            try:
                # 1. Fetch historical candles
                is_crypto = symbol.endswith("USDT")
                if is_crypto:
                    candles = crypto_provider.fetch_ohlc(symbol, interval, limit=1000)
                else:
                    candles = stock_provider.fetch_ohlc(symbol, interval, range_days=days)
                
                if not candles or len(candles) < 300: continue

                # 2. Extract S&R for context
                import indicators
                sr_levels = indicators.compute_sr_levels(candles, interval)

                # 3. Engineer Features
                df = self.ai_engine.engineer_features(candles, sr_levels)
                
                # 4. Create Labels (Bullish outcome in next 10 candles)
                # target = 1 if price increases by 2% in next 10 candles, else 0
                df['future_max'] = df['close'].shift(-10).rolling(window=10).max()
                df['target_bullish'] = np.where(df['future_max'] > df['close'] * 1.02, 1, 0)

                # Clean up
                df = df.dropna().drop(['future_max'], axis=1)
                
                features = df.drop(['time', 'open', 'high', 'low', 'close', 'volume', 'ema20', 'ema50', 'ema200', 'target_bullish'], axis=1, errors='ignore')
                labels = df['target_bullish']

                all_features.append(features)
                all_labels.append(labels)
            except Exception as e:
                print(f"[Trainer] Error processing {symbol}: {e}")

        if not all_features: return None, None
        
        X = pd.concat(all_features).reset_index(drop=True)
        y = pd.concat(all_labels).reset_index(drop=True)
        return X, y

    def train_xgboost(self, X, y):
        """
        Trains the XGBoost model using Time-Series Cross-Validation.
        """
        tscv = TimeSeriesSplit(n_splits=5)
        
        print("[Trainer] Starting Time-Series Cross-Validation...")
        
        metrics = []
        best_model = None
        best_f1 = 0

        for train_index, test_index in tscv.split(X):
            X_train, X_test = X.iloc[train_index], X.iloc[test_index]
            y_train, y_test = y.iloc[train_index], y.iloc[test_index]

            model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                use_label_encoder=False,
                eval_metric='logloss'
            )

            model.fit(X_train, y_train)
            
            y_pred = model.predict(X_test)
            f1 = f1_score(y_test, y_pred)
            
            if f1 > best_f1:
                best_f1 = f1
                best_model = model
            
            metrics.append({
                "accuracy": accuracy_score(y_test, y_pred),
                "precision": precision_score(y_test, y_pred),
                "recall": recall_score(y_test, y_pred),
                "f1": f1
            })

        print(f"[Trainer] Best F1 Score: {best_f1:.4f}")
        
        # Save Model
        if best_model:
            model_path = os.path.join(self.models_dir, "xgboost_quant_v1.joblib")
            joblib.dump(best_model, model_path)
            print(f"[Trainer] Model saved to {model_path}")
        
        return metrics

if __name__ == "__main__":
    # Test training run
    engine = AIEngine()
    trainer = ModelTrainer(engine)
    
    test_symbols = ["BTCUSDT", "ETHUSDT", "RELIANCE.NS", "TCS.NS", "INFY.NS"]
    X, y = trainer.prepare_training_data(test_symbols)
    
    if X is not None:
        metrics = trainer.train_xgboost(X, y)
        print("[Trainer] Training Complete.")
        print(pd.DataFrame(metrics).mean())
    else:
        print("[Trainer] Not enough data to train.")
