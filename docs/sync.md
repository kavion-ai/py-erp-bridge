# Incremental synchronization

```python
from py_erp_bridge import IncrementalSync, InMemorySyncState

sync = IncrementalSync(erp.customers, InMemorySyncState())
report = await sync.run(upsert)
```

Delivery is at least once: the watermark advances only when every record was handled, and an overlap
(5 minutes by default) is read again. Handlers must be idempotent. Deletions are not detected.

Persist state by implementing `SyncStateStore`; see `examples/incremental_sync.py`. Use a separate `key` per
tenant. Providers that cannot sort need `run(handler, ordered=False)`. Timestamps without an offset are
taken as UTC; if your ERP reports local time, use an overlap at least as large as the UTC offset.

An entity needs `modified_at` mapped and `modified_field` set; add them with `entity_overrides` if missing.
