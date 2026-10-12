# Distribution Notes

Last checked: 2026-06-14

Future Chart currently uses a web-based subscription flow:

- user creates/logs into a Future Chart account
- user opens `/checkout`
- Razorpay hosted checkout handles UPI/card payment
- Razorpay webhook verifies payment server-side
- backend activates premium status in the app database

This is suitable for a browser-hosted education product or companion website. If Future Chart is later distributed as a native app or native wrapper through app stores, billing rules must be rechecked before launch.

## Google Play

Google Play generally requires Google Play Billing for in-app purchases of digital content and subscriptions in Play-distributed Android apps, unless an eligible alternative billing program applies.

Policy source: https://support.google.com/googleplay/android-developer/answer/9858738

Action before Android launch: decide whether the app is browser-only, PWA, or Play-distributed. If Play-distributed, replace or supplement Razorpay checkout with Play Billing where required.

## Apple App Store

Apple App Review Guidelines require clear pricing, subscription disclosures, working in-app purchase metadata where applicable, and appropriate licensing for apps that perform financial activity. Future Chart must remain education/paper-trading only unless a properly licensed entity operates real financial services.

Policy source: https://developer.apple.com/app-store/review/guidelines/

Action before iOS launch: confirm whether Apple In-App Purchase is required for the subscription features in the target distribution model, and avoid linking to external payment inside a native iOS app unless allowed by the applicable storefront rules.
