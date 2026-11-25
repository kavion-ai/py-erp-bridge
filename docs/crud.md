# CRUD operations

```python
customer = erp.customers.get("1001")
customer = erp.customers.create({"name": "Acme"})
customer = erp.customers.update("1001", {"email": "ap@example.com"})
erp.customers.delete("1001")
```

Entities: `customers`, `vendors`, `products`, `inventory`, `sales_orders`, `purchase_orders`,
`invoices`, `payments`, `employees`, `financial_records`.

- Only fields you set are sent. `None` clears a field.
- Unknown fields raise `PayloadValidationError`; fields the provider has no mapping for raise
  `MappingError`. Put native fields into `provider_fields`.
- Some ERPs return only the new id. `create(..., refresh=True)` reads the record back.
- `update(..., etag=...)` detects concurrent changes where the provider supports it.
- Unsupported operations raise `UnsupportedOperationError`. Check with `erp.capabilities()` or
  `erp.customers.supports(Operation.DELETE)`.

```python
erp.sales_orders.create(
    {
        "customer_id": "1001",
        "items": [{"product_id": "A", "quantity": "2"}],
        "provider_fields": {"SalesOrderType": "OR"},
    }
)
```

Composite keys are mappings or `a/b` strings:

```python
erp.customers.get({"dataAreaId": "usmf", "CustomerAccount": "US-001"})
erp.customers.get("usmf/US-001")
```

## Custom entities

```python
from py_erp_bridge import EntitySpec

tickets = erp.entity("tickets", spec=EntitySpec(path="Issue", key_fields=("name",)))
tickets.create({"subject": "Printer"})  # provider-native fields
print(tickets.get_all()[0].raw)
```

## Escape hatches

```python
erp.execute("suiteql", query="SELECT id FROM customer")  # provider operations
erp.request("GET", "/any/path", params={"a": "b"})  # raw HTTP, same auth and resilience
erp.run(erp.adapter.health_check())  # adapter coroutines
```
