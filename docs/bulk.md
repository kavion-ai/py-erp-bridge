# Bulk operations

```python
result = erp.customers.bulk_create([{"name": "A"}, {"name": "B"}])
for item in result.failed:
    print(item.index, item.error, item.skipped)
```

All inputs are validated before anything is sent. `raise_on_failure=True` raises `PartialFailureError`
with the result; `stop_on_error=True` creates one by one and skips the rest after a failure.

| Provider | Mechanism | Atomic |
|---|---|---|
| S/4HANA, Dynamics 365 | `$batch`, 100 per request | no; `execute("create_atomic", ...)` is |
| Odoo | One `create` call | yes |
| Infor M3 | 100 transactions per request | no |
| Others | One request per record, bounded by `max_concurrency` | no |

A lost reply marks every record of that request as failed although some may exist: look them up before
retrying. Bulk updates and deletes are not provided; loop over single operations.
