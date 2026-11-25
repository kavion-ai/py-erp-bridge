# Custom provider development

Subclass `ERPAdapter`, describe it with `ProviderInfo`, and implement `list_page`, `get` and `health_check`;
add `create`, `update`, `delete`, `bulk_create` as needed. `examples/custom_provider.py` is complete.

```python
from py_erp_bridge import register_provider

register_provider("example_erp", ExampleAdapter)  # or "package.module:ExampleAdapter"
```

Packages can register through an entry point instead:

```toml
[project.entry-points."py_erp_bridge.providers"]
example_erp = "my_package:ExampleAdapter"
```

Guidelines:

- Exchange raw payloads; mapping happens in the core.
- Use `self.http.request`: it adds auth, retries, limits and error mapping. Pass `idempotent=True` for
  POSTs that only read.
- Use `self.record_list(...)` so that an unexpected body is not read as an empty list.
- Raise `self.unsupported(...)` for anything you cannot express, and escape or reject filter values.
- Override `parse_error` to extract codes and messages.
- Mark provider-specific coroutines with `@operation` to expose them through `execute`.
- For OData, subclass `py_erp_bridge.providers._odata.ODataAdapter`.

`tests/providers/test_contract.py` shows what adapters are expected to fulfil.
