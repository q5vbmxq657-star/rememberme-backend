# Pricing implementation checkpoint

## Implemented

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

1. Confirm Plus prices, paid avatar limits and credit-pack prices/quantities.
2. Configure actual App Store products, subscription groups and account binding.
3. Wire the signed-transaction verifier and durable registry to authenticated purchase submission and current App Store status. Expose signed server-notification processing only once all subscription event types have durable handling, and add financial refund reconciliation. The registry preserves revocations but does not yet reverse granted credits. Signature evidence alone does not prove current entitlement status.
4. Integrate personal balances and verified call usage; complete refund handling.
5. Connect the period scheduler to verified subscriptions and a durable job runner.
6. Define the purchase channel and authorization for auto-recharge; no silent consumable purchase is implemented.
7. Enforce Free limits atomically only with a working upgrade/recovery path and an existing-user migration decision.
8. Run Sandbox purchase, restore, renewal, refund and multi-device acceptance.
9. Finalize billing-record retention and deletion policy before production activation; detached anti-replay records are not a completed retention contract.

Family approved prices remain EUR 59.99 monthly / EUR 599.99 annually. Actual
localized purchase prices must come from StoreKit, not this document. No paid
products are fabricated and no production billing was activated.
