"""Documentation must match the code."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from py_erp_bridge import available_providers, registry

ROOT = Path(__file__).parent.parent
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_capabilities_are_current():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts/generate_capabilities.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TARGET.read_text("utf-8") == module.render()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_relative_links_resolve(page):
    for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)#]+)", page.read_text("utf-8")):
        assert (page.parent / target).exists(), target


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_python_snippets_parse(page):
    for code in re.findall(r"```python\n(.*?)```", page.read_text("utf-8"), re.S):
        compile(code, str(page), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_every_provider_has_docs_and_example():
    for name in set(available_providers()) - {"example_erp"}:
        if registry.get(name).__module__.startswith("py_erp_bridge."):
            assert (ROOT / f"docs/providers/{name}.md").exists()
            assert (ROOT / f"examples/{name}.py").exists()
