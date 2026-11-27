# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"
ruff format . && ruff check . && mypy && pytest       # full check (CI uses `ruff format --check`)
pytest tests/providers/test_odata_providers.py         # one file
pytest tests/unit/test_core.py::test_name              # one test
pytest -k "netsuite"                                   # by keyword (contract tests are parametrized by provider)
python scripts/generate_capabilities.py                # regenerate docs/capabilities.md after changing an adapter
python -m build && twine check --strict dist/*         # packaging
```

- pytest runs with `filterwarnings = ["error"]`, `asyncio_mode = "auto"` and `pythonpath = ["tests"]` (tests import `from helpers import ...`).
- mypy is `strict` over `src` and `examples`. Python 3.11+, ruff line length 100, docstrings required outside tests/examples.

## Architecture

Status is alpha: every adapter is tested only against mocked servers, none against a live ERP.

Layers (`src/py_erp_bridge/`): `client` (public sync/async clients, tenants) → `core` (adapter contract, query, mapping, bulk, sync, registry) → `providers` (one adapter per ERP) → `transport` (HTTP, OData, XML/SOAP, RFC) with `auth` and `resilience` (retry, circuit breaker, rate limits) applied inside the HTTP transport. `models` holds canonical Pydantic records; `config` validates settings.

Key points that span files:

- **One async implementation.** `AsyncERPClient` is the real implementation; `ERPClient` runs it on a private event-loop thread (`LoopThread` in `client.py`). Never add a separate sync code path.
- **Core never references a provider.** Provider-specific logic stays in `providers/`. Adapters exchange *raw* payloads; mapping to/from canonical models happens in the core (`core/mapping.py`, `core/transforms.py`).
- **Mappings are data.** Each adapter declares `default_entities: dict[str, EntitySpec]` (`core/capabilities.py`): path, key fields, supported `operations`, `FieldMap`s, line items, `modified_field`, and an `evidence` level (`official_docs` / `secondary` / `unconfirmed`). Tenants can override specs via config. Canonical entity names/models live in `client.CANONICAL_MODELS`.
- **Adapter contract** (`core/adapter.py` `ERPAdapter`): implement `list_page`, `get`, `health_check`; optionally `create`/`update`/`delete`/`bulk_create`/`upload_file`/`register_webhook`. Describe it with `ProviderInfo`. Use `self.http.request` (adds auth, retries, limits, error mapping; pass `idempotent=True` for read-only POSTs), `self.record_list(...)` (so an unexpected body never becomes an empty list), `self.unsupported(...)` for anything inexpressible, override `parse_error`, and mark extra coroutines with `@operation` to expose them via `execute`. OData providers (S/4HANA, Dynamics 365) subclass `providers/_odata.py:ODataAdapter`.
- **Registry.** Built-ins are registered lazily by `"module:Class"` string in `providers/__init__.py`; third parties via `register_provider` or the `py_erp_bridge.providers` entry-point group (explicit registrations win over entry points).
- **Retry safety.** Non-idempotent requests (creates) are retried only when the request was certainly not sent; `TransportError.request_sent` signals unknown outcome.

## Rules (from CONTRIBUTING and design decisions)

- Never invent ERP APIs or wire details. Set `evidence` to what was actually checked and link the source.
- Refuse rather than guess: a field, filter or sort a provider can't express must raise, never be dropped silently. Escape or reject filter values.
- Every adapter change needs wire-level tests (request and response) using `tests/helpers.py` (`MockServer` passed as `http_transport=`, `config(provider)` with zero-delay retries). `tests/providers/test_contract.py` checks invariants for every adapter (declared writes implemented, mappings refer to real canonical fields, etc.).
- `docs/capabilities.md` is generated — don't edit by hand; `tests/test_docs.py` fails if it is stale. That test also checks relative links in `README.md`/`docs/`, that Python snippets in docs compile, and that every built-in provider has `docs/providers/<name>.md` and `examples/<name>.py`.
- SAP ECC uses `pyrfc` (not a pip extra; see `docs/sap-rfc.md`); tests fake the RFC connection instead of requiring it.
- If an adapter is ever run against a real sandbox, record release and date in `docs/status.md`.
