# Future App

Future App is a local Flask + vanilla JavaScript trading education and paper trading simulator. It is intended for students to learn market mechanics, charting, indicators, backtesting, and simulated order management with virtual money.

It is not investment advice, not a real-money trading system, and does not promise returns. AI and pattern features are educational analysis aids only.

## What It Includes

- Interactive candlestick charting with Lightweight Charts.
- Paper trading and simulated futures mechanics.
- Watchlists, alerts, screener, indicators, and backtesting tools.
- Optional AI commentary using Gemini or Claude when an administrator provides API keys.
- Local JSON/SQLite-style runtime data and yfinance/NSE/crypto market data providers.

## Tech Stack

- Backend: Python, Flask, Flask-CORS, pandas/numpy/scipy, yfinance, XGBoost model loading.
- Frontend: vanilla JavaScript, HTML, CSS, Lightweight Charts.
- Tests: pytest for the trading engine and pattern engine.

There is no FastAPI service in the current application.

## Security Setup

Never commit real secrets. The local `backend/.env` file is ignored by git.

1. Copy the example file:

   ```powershell
   Copy-Item backend\.env.example backend\.env
   ```

2. Fill only the keys you need:

   ```env
   GEMINI_API_KEY=
   ANTHROPIC_API_KEY=
   FLASK_SECRET_KEY=
   RAZORPAY_KEY_ID=
   RAZORPAY_KEY_SECRET=
   SMTP_USER=
   SMTP_PASS=
   ```

3. If a key was ever committed, pasted into chat, or shared publicly, rotate it immediately in the provider dashboard. Treat exposed keys as compromised.

## Run Locally

Windows:

```powershell
.\start.ps1
```

Or double-click:

```text
run.bat
```

The app binds to `127.0.0.1:5000` by default. To bind elsewhere:

```powershell
$env:APP_HOST="127.0.0.1"
$env:APP_PORT="5000"
.\start.ps1
```

Debug mode is disabled by default and is only allowed when `APP_HOST` is local:

```powershell
$env:FLASK_DEBUG="1"
.\start.ps1
```

## Run With Docker

Build and run:

```bash
docker build -t future-app .
docker run --env-file backend/.env -p 5000:5000 future-app
```

The Docker image starts Flask on `0.0.0.0:5000` with debug mode disabled.

## Accounts and Subscriptions

The app includes a basic email/password account flow and a Razorpay-ready subscription foundation. Local development stores users/subscriptions in `backend/data/*.json`; production should replace this with a managed database before launch.

The current premium placeholder is `Student Premium` at `₹299/month`. This is a product placeholder, not a final business decision. Before accepting real payments, finalize:

- pricing and billing cycle
- GST/tax invoice handling
- refund and cancellation policy
- production authentication method, such as email OTP or phone OTP
- Razorpay Checkout frontend integration and webhook handling

## Tests

```powershell
python -m pytest backend\tests -q
```

## Model Evaluation

AI/model output is disabled by default unless an administrator supplies a key or local model file. Do not market the XGBoost or pattern features as predictive unless you have run a documented historical evaluation.

Use the offline evaluator with your own OHLCV candle JSON:

```powershell
python backend\model_evaluation.py path\to\candles.json --model-path models\xgboost_quant_v1.joblib
```

The evaluator reports historical simulation metrics such as sample count, accuracy, win rate, average forward return, and max drawdown. These are educational backtest metrics only; they do not predict future results.

## Product Positioning

Future App should be presented as a student-facing learning tool:

- simulated/virtual money only
- educational use only
- not investment advice
- not SEBI-registered
- AI commentary is optional and generated for discussion, not recommendation

## Pre-Launch Checklist

See `docs/prelaunch_checklist.md` for app-store policy notes, legal placeholders, payment requirements, and final launch blockers.
