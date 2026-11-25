# Implementation status

## Implemented and tested with mocks

- [x] Core: adapter contract, registry, entry points, queries, mapping, overrides, custom entities
- [x] Sync and async clients, tenants
- [x] All authentication types
- [x] Retry, circuit breaker, rate limit, TLS, proxy setting, logging, metrics
- [x] OData v2 and v4, `$batch`, CSRF; XML; SOAP 1.1; JSON-RPC; RFC (with a fake connection)
- [x] Ten adapters, with contract tests and wire-level tests
- [x] Bulk creation, incremental sync, webhooks, file upload (Odoo, ERPNext)
- [x] Examples: their read paths run in the test suite against a local HTTP server

## Not verified

- [ ] **No provider has been run against a live or sandbox system.** Mocks encode the same
      assumptions as the adapters, so passing tests do not prove that a vendor's API behaves that way.
- [ ] Entity paths and fields marked `unconfirmed` or `secondary` in [capabilities](capabilities.md)
- [ ] Wire formats of Sage Intacct (site refused access), Infor M3 (no official page found), Oracle Fusion
      (reference not readable); S/4HANA entity sets (Business Accelerator Hub needs JavaScript)
- [ ] The write paths of the examples
- [ ] RFC with a real SAP NW RFC SDK; mutual TLS and proxies against real endpoints

## Not implemented

- Idempotency keys by default: no provider's documentation that was read names a header, so
  `idempotency_header` is opt-in
- Webhook registration except ERPNext; file upload except Odoo and ERPNext
- Bulk update and delete; deletion detection in incremental sync
- Business Central, Dataverse, Infor LN and SyteLine, Intacct XML API, Tally JSON, Fusion FBDI,
  IDocs, NetSuite asynchronous requests
- ECC creation from canonical fields (BAPI parameters are passed through)
- Vendor (accounts payable) invoices as a canonical entity; use a custom entity

## Live verification log

| Provider | Release | Date | Scope |
|---|---|---|---|
| - | - | - | none yet |
