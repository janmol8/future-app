# Future Chart Pre-Launch Checklist

Last checked: 2026-06-14

## Positioning

- Present the app as a student education and paper trading simulator.
- Do not claim investment advice, recommendations, research, brokerage, or guaranteed returns.
- Keep visible disclaimers in the app, pricing page, model lab, terms, and privacy policy.

## Store Policy Notes

- Google Play payments: digital in-app features and subscriptions generally must use Google Play Billing for Play-distributed Android apps, unless an eligible alternative billing program applies. Source: Google Play Payments policy, checked 2026-06-14.
- Apple review: apps must provide safe, non-misleading experiences, complete metadata, working demo access for review, and detailed notes for in-app purchases. Source: Apple App Review Guidelines, checked 2026-06-14.
- Apple financial activity: trading or investing functionality must be submitted by the financial institution performing the services. Because Future Chart is positioned as education/paper trading only, avoid real trading, brokerage, real-money execution, or advisory claims.
- Apple AI/privacy: disclose where personal data is shared with third parties, including third-party AI, and obtain explicit permission before doing so.

## Required Before Real Launch

- Replace unofficial market data sources with a licensed or permitted feed before charging users: the NSE website adapter (`nse_site`) and yfinance are unlicensed for redistribution; switch to a broker API on your own account or an authorised vendor agreement. See docs/DATA_SOURCES.md and docs/UPGRADE_PLAN.md. (Added 2026-09-28.)
- Replace placeholder legal contact details in `frontend/privacy.html` and `frontend/terms.html`.
- Finalize pricing, billing cycle, refund policy, cancellation flow, GST/tax invoicing, and support SLA.
- Replace local JSON auth/subscription storage with a production database.
- Add Razorpay Checkout frontend integration, server-side webhooks, and payment reconciliation.
- For Google Play distribution, replace external Razorpay in-app purchase flow with Play Billing unless enrolled in an eligible alternative billing program.
- For iOS App Store distribution, confirm whether the subscription must use Apple In-App Purchase for the target storefront and app packaging.
- Provide app review demo credentials and keep backend services reachable during review.
- Run full clean-install test using only `.env.example` values filled in.

## Verification Commands

```powershell
python -m pytest backend\tests -q
python -m compileall -q backend
node --check frontend\js\app.js
```
