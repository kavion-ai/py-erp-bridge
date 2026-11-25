# Contributing

```bash
python -m venv .venv
pip install -e ".[dev]"
ruff format . && ruff check . && mypy && pytest
python scripts/generate_capabilities.py   # after changing an adapter
```

- Never invent APIs. Set `evidence` to what you actually checked, and link the source.
- Keep provider logic out of `core`.
- Refuse what cannot be expressed; never drop fields or filters silently.
- Add wire-level tests (request and response) for every change to an adapter.
- Never commit credentials or customer data, not even in tests.
- If you run an adapter against a real sandbox, record release and date in `docs/status.md`.
