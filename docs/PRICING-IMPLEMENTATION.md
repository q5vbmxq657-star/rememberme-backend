# Pricing implementation checkpoint

## Current release gate - 2026-10-05, after production deployment

This section supersedes the historical implementation checkpoints below.

- Backend commit `73fe82c` was deployed successfully to Production. Health and
  pricing returned HTTP 200; the catalog still reported `purchases_available=false`.
- iOS commit `58a3b71` was pushed to `main`. This does not constitute a TestFlight
  upload or device acceptance of that source version.
- Subsequent local review fixes guard StoreKit state against account changes and
  recheck the server purchase gate before opening Apple's purchase sheet. They
  also treat empty Apple status/transaction responses as retryable failures,
  without granting credits. These review fixes are not yet deployed.
- Backend review validation: 145 tests passed, including isolated PostgreSQL
  fulfillment, refund, concurrency and annual scheduler failure tests.
- Purchases must remain disabled: refund reversals, full entitlement reconciliation,
  and measured call settlement are not complete end to end. Successful database
  tests do not establish successful Apple purchase delivery.
- The last recorded external checks remain unresolved: Production Apple API 401,
  missing Sandbox notification URL, incomplete App Store product release metadata,
  and no real purchase/restore/renewal/refund acceptance. Recheck rather than infer
  that deployment resolved them. Confirm developer membership renewal as well.
- Annual scheduling remains opt-in via `STAY_APPLE_ALLOWANCE_WORKER_ENABLED`.
  Do not enable it or claim billing release readiness from the health endpoint.

## Historical checkpoints

## Implemented

- Authenticated billing-account and signed-transaction intake routes connect the existing durable registry to iOS. They never treat evidence storage as fulfillment (`fulfilled` remains false).
- Transaction intake now checks Apple's current subscription status through the official server API before recording evidence. App/environment, original transaction, active paid status and freshly verified transaction terms must match. Expired, revoked, replaced or ambiguous results do not authorize an allowance. API failure is retryable, not success. This is a point-in-time check, not durable reconciliation or refund fulfillment.
- iOS StoreKit 2 loads configured product IDs, uses Apple's localized prices, binds purchases through the server account token, handles pending/canceled purchases, and supports explicit restoration. One app-wide purchase owner observes updates and unfinished transactions. Transactions are only finished after server-confirmed fulfillment.

- Read-only, uncached plan catalog for Free, Plus and Family.
- Free terms: one avatar, Memory Journey through level five, ten weekly chat messages.
- Plus allowance: 100 monthly credits. Family allowance: 300 monthly credits.
- Existing Family ledger uses the canonical credit conversion constants.
- Monthly allowance scheduling supports annual subscriptions without granting future months up front.
- Month-end scheduling preserves the original calendar anchor, including leap years.
- Stable period evidence keys isolate Sandbox from Production and prevent repeat grants through the existing ledger.
- iOS plan comparison loads the backend catalog and has loading/retry states.
- Apple purchase evidence verification uses the official App Store Server Library 3.1.2 with online certificate checks enabled. Bundle, environment, configured product, ownership, account token, expiry, revocation and upgrade checks are enforced.
- Invalid signature input is tested through the real Apple verifier. Positive contract tests substitute decoded fixtures and do not count as App Store Sandbox acceptance.
- Migration 039 adds durable billing account tokens, subscription ownership, immutable transaction terms and revocation tombstones. Concurrent restore/refund processing is serialized in PostgreSQL. Deleting a user detaches the billing token rather than making their purchases claimable by another account.
- Migration 040 adds idempotent revocation-notification storage. The verifier checks both outer and nested Apple signatures for REFUND/REVOKE, and the registry records each notification and its revocation atomically. Other event types, including REFUND_REVERSED, still require the pending status reconciliation flow; they are not silently acknowledged as processed.

## Not enabled or complete

The catalog is not an entitlement authority. Displaying a plan never authorizes
features or grants credits. Purchases remain unavailable; Video is coming soon.

`monthly_credit_periods` is an internal scheduler, not an Apple receipt verifier.
It must only receive a server-verified paid interval after ownership, revocation,
environment and product checks. Its results must not be accepted from clients.
Historical catch-up requires refund/revocation reconciliation before granting.

Required before charging customers:

1. Confirm paid avatar limits and credit-pack prices/quantities. Plus and Family subscription prices are approved below.
2. Configure actual App Store products, subscription groups and account binding.
3. Wire the signed-transaction verifier and durable registry to authenticated purchase submission and current App Store status. Expose signed server-notification processing only once all subscription event types have durable handling, and add financial refund reconciliation. The registry preserves revocations but does not yet reverse granted credits. Signature evidence alone does not prove current entitlement status.
4. Integrate personal balances and verified call usage; complete refund handling.
5. Connect the period scheduler to verified subscriptions and a durable job runner.
6. Define the purchase channel and authorization for auto-recharge; no silent consumable purchase is implemented.
7. Enforce Free limits atomically only with a working upgrade/recovery path and an existing-user migration decision.
8. Run Sandbox purchase, restore, renewal, refund and multi-device acceptance.
9. Finalize billing-record retention and deletion policy before production activation; detached anti-replay records are not a completed retention contract.

On 2026-10-05 the user approved Plus at EUR 59.99 monthly / EUR 599.99 annually
and authorized creating all subscriptions in App Store Connect. Family retains
the same approved prices. The following actual products were created under app
6780634832, group `STAY Membership` (22441685):

| Product ID | Apple ID | German base price |
| --- | --- | --- |
| `stay.plus.monthly` | 6819201355 | EUR 59.99 / month |
| `stay.plus.annual` | 6819202981 | EUR 599.99 / year |
| `stay.family.monthly` | 6819203610 | EUR 59.99 / month |
| `stay.family.annual` | 6819204338 | EUR 599.99 / year |

Prices were saved through Apple's confirmation dialog. English (US) group and
product localizations were saved. Annual descriptions explicitly retain monthly
credit allowances; they do not promise a year of credits up front. Products remain
in preparation, not submitted or approved. Availability, review screenshots and
subscription levels still need completion: Family monthly/annual must share the
higher service level, and Plus monthly/annual the lower level. The initial four
creation-order levels must not be released unchanged. Apple Family Sharing was
not enabled; the STAY family membership is a separate server-side feature.

Deployment must supply this exact `STAY_APPLE_PRODUCTS` mapping together with
verified bundle/environment/certificate configuration:

```json
{
  "stay.plus.monthly": {"plan": "plus", "cadence": "monthly"},
  "stay.plus.annual": {"plan": "plus", "cadence": "annual"},
  "stay.family.monthly": {"plan": "family", "cadence": "monthly"},
  "stay.family.annual": {"plan": "family", "cadence": "annual"}
}
```

Actual localized purchase prices must come from StoreKit, not this document.
Production billing was not activated. App Store Connect also displayed developer
membership expiration on 2026-10-08; renewal must be confirmed before release.

The status check requires `STAY_APPLE_KEY_ID`, `STAY_APPLE_ISSUER_ID`, and
exactly one of `STAY_APPLE_PRIVATE_KEY_PATH` (securely mounted key) or
`STAY_APPLE_PRIVATE_KEY` (private server environment secret). Ambiguous sources
and invalid/non-P-256 keys fail closed.
No signing key is included in the repository or requested in chat. Missing
configuration returns an actionable 503 and cannot enable purchases.

Apple signing-key inventory (verified 2026-10-05):
- Active key name: `STAY Billing Production`; key ID: `38G9GB3FGG`.
- Issuer ID: `2bc872eb-b3e6-4757-91f1-328e804f4b65`.
- Replacement downloaded successfully through Safari; private-key validity checked
  with OpenSSL and local permissions restricted to owner read/write (`0600`).
- Previous STAY key `FL245F3S42` was revoked. Do not configure it.
- The replacement and app/key identifiers are stored in Railway's `web` service,
  `production` environment, with automatic deployment suppressed. A read-back
  comparison confirmed that the secret matches the downloaded key without logging
  either value. The running deployment has not been restarted or updated.
- Private key material must remain outside this repository and logs.

The current status check does not grant grace-period credits, perform historical
refund catch-up, or replace signed server notifications. These remain separate
release requirements. Focused billing/verifier/period/catalog tests: 66 passed.

Live connection check on 2026-10-05:
- Production test-notification request: HTTP 401, repeated once with the same result.
  The underlying cause is not yet established; do not interpret this as a passing
  production authentication check.
- Sandbox test-notification request: HTTP 404, Apple error 4040007
  (`SERVER_NOTIFICATION_URL_NOT_FOUND`). No successful callback delivery occurred.
- The local HTTP receiver `/v1/billing/apple/notifications` verifies signed V2
  envelopes and handles TEST plus REFUND/REVOKE tombstones. Revocations commit
  before acknowledgement and replay is idempotent. It is not deployed or
  registered in App Store Connect yet. Renewal and refund-reversal events return
  503 deliberately; they must not be acknowledged until reconciliation exists.
- No StoreKit purchase or credit fulfillment acceptance was performed. Purchases
  remain unavailable and transaction intake still returns `fulfilled: false`.
- Focused tests after adding server-secret loading: 71 passed. These tests are not
  a substitute for live purchase, refund, renewal, or credit-ledger acceptance.

Notification receiver validation (2026-10-05): 126 tests passed, including isolated
PostgreSQL integration tests for notification replay, concurrent restore/refund,
ownership, and Family accounting. Invalid signatures and envelopes cannot reach
storage; database failure returns a retryable response without database details.
These tests establish revocation persistence, not financial refund settlement.

Family transaction intake now grants elapsed monthly allowances atomically with
purchase evidence. Each grant is linked to the Apple transaction, and verified
REFUND/REVOKE notifications reverse its grants exactly once. Sandbox grants are
excluded from production balances. Consumed refunded credits remain accounting
debt; available units cannot become negative. Subscription-to-Family bindings
survive Family deletion so recreating a Family cannot replay its paid periods.
These changes require migrations 041 and 042, applied only to the isolated test
database so far. They are not deployed.

A concurrent Restore test exposed a user-row lock upgrade deadlock. Billing
account creation now uses an account advisory lock and KEY SHARE protection
against deletion, without upgrading the Family transaction's user SHARE lock.
The combined database and unit suite passes 131 tests after this correction.

Personal Plus credit fulfillment now shares the same journal and refund logic as
Family. Migration 043 adds account-owned entries with an exclusive owner
constraint. `/v1/billing/credits` exposes only the authenticated user's production
balance, with no caching. The iOS plans screen now displays this personal balance
outside the onboarding paywall, refreshes on foreground/account/purchase changes,
and rejects stale overlapping responses. Missing balances are not displayed as zero.
Its decoder supports refund debt and checks arithmetic overflow.

SUBSCRIBED and DID_RENEW notifications now verify the signed envelope and nested
transaction, fetch current transaction evidence from Apple, and atomically record
the purchase plus credit allocation. Past paid intervals are accepted only on
this reconciliation path, not as active purchases. Notification processing does
not require a live user session and replay cannot allocate the same period twice.
The old `apple_family_fulfillment` module was replaced by `apple_credit_fulfillment`;
there is one fulfillment implementation, not separate Plus/Family pipelines.

Payment activation remains blocked by unfinished software as well as external
acceptance: refund-reversal reconciliation,
full entitlement reconciliation, and verified call usage
settlement are not connected end to end. Notification endpoints/configuration and
these local changes have not been deployed or accepted with a real StoreKit purchase.

Annual allowance scheduling now runs in the existing application lifespan when
`STAY_APPLE_ALLOWANCE_WORKER_ENABLED=true`. Migration 044 stores due times and
leases. Workers claim one transaction using SKIP LOCKED, refresh Apple evidence,
and reuse the same idempotent fulfillment path. Successful active annual purchases
are checked hourly; failed attempts retry after 15 minutes. A crashed worker's
claim is recoverable after five minutes. Completed paid intervals stop polling
after catch-up. The flag is not enabled in Production. Backend validation: 140
tests passed, including isolated PostgreSQL catch-up and provider-failure tests.
Do not change `purchases_available` or return `fulfilled: true` before that work
and StoreKit sandbox acceptance are complete.
