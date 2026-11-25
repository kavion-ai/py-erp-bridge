# OData

S/4HANA uses OData v2, Dynamics 365 uses v4.

| Feature | Behaviour |
|---|---|
| Literals | Escaped and typed per version (`datetime'...'`, `...Z`) |
| Paging | `__next` / `@odata.nextLink`, else `$skip` / `$top` ordered by key |
| Next links | Always sent to the host of `base_url`, whatever host they name |
| Expand | `EntitySpec.extra["expand"]` |
| Concurrency | `If-Match: *`, or your `etag` |
| CSRF (SAP) | Fetched per service, renewed once when rejected |
| Batch | `bulk_create`: one changeset per record; `create_atomic`: one for all |

Any service is reachable:

```python
from py_erp_bridge import EntitySpec

materials = erp.entity(
    "stock", spec=EntitySpec(path="API_PRODUCT_SRV/A_ProductPlant", key_fields=("Product", "Plant"))
)
materials.get_all(filters={"Plant": "1010"}, params={"$expand": "to_ProductPlantMRP"})
```

If a service cannot sort by its keys, set `extra={"default_sort": "false"}`.

Custom `EntitySpec.key_fields` are treated as strings. For numeric or GUID keys use `erp.request()`.
