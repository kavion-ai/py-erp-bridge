# Synchronous and asynchronous usage

```python
from py_erp_bridge import ERPClient

with ERPClient("odoo", config) as erp:
    customers = erp.customers.get_all()
```

```python
async with ERPClient.create_async("odoo", config) as erp:
    async for customer in erp.customers.iterate():
        ...
```

Both offer the same methods. The blocking client can be shared between threads and also works inside a
running event loop, where it blocks that loop; prefer the async client there. Use a client only in
the process that created it, so create it after forking. Use an async client in one event loop.
