# Pagination and filtering

```python
for invoice in erp.invoices.iterate(limit=1000):  # lazy, page by page
    ...

invoices = erp.invoices.get_all(limit=1000)  # in memory

page = erp.invoices.list_page(page_size=50)
while page.next_cursor:
    page = erp.invoices.list_page(page_size=50, cursor=page.next_cursor)
```

Cursors are opaque, and valid only for the same query. Page sizes are capped at the provider's
maximum. With offset paging, records that change during iteration can be skipped or repeated; sort by
a stable field, or use [incremental sync](sync.md).

## Filters

```python
from py_erp_bridge import Filter, FilterOp

erp.invoices.get_all(filters={"customer_id": "1001", "total__gt": 100, "status": ["open", "late"]})
erp.invoices.get_all(filters=[Filter(field="number", op=FilterOp.STARTSWITH, value="INV")])
erp.invoices.get_all(sort=["-invoice_date"], select=["number", "total"])
```

Operators: `eq ne gt ge lt le in contains startswith`. Conditions are AND-ed. Names that are not
canonical are passed through as provider fields. What the provider cannot express raises
`UnsupportedOperationError`; nothing is dropped silently. Values are escaped, or rejected if the
provider documents no way to escape them.

`params={...}` adds native query parameters.

TallyPrime filters locally after reading the whole collection.
