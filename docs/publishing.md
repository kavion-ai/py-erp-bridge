# Deployment and publishing to PyPI

Nothing is published automatically.

## 1. Prepare

Update `src/py_erp_bridge/_version.py` (the only place holding the version) and `CHANGELOG.md`, then run:

```bash
pip install -e ".[dev]"
ruff format --check . && ruff check . && mypy && pytest
```

## 2. Build and validate

```bash
python -m build
twine check --strict dist/*
```

## 3. TestPyPI

```bash
twine upload --repository testpypi dist/*
python -m venv /tmp/t && . /tmp/t/bin/activate
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ py-erp-bridge
python -c "import py_erp_bridge; print(py_erp_bridge.__version__, py_erp_bridge.available_providers())"
```

Only use the extra index for this smoke test: mixing indexes allows dependency confusion.

## 4. PyPI

Use a project-scoped token, or trusted publishing from CI.

```bash
twine upload dist/*
```

## 5. Verify

In a fresh environment run `pip install py-erp-bridge==X.Y.Z`, repeat the smoke test, check the project page,
and tag the release `vX.Y.Z`. A version can never be uploaded twice; yank a broken one.

## Before the first release

Check that the name is still free, replace the generic author in `pyproject.toml`, add project URLs, and
confirm that the MIT license is what you want.
