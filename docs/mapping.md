# Data transformation and mapping

A `FieldMap` connects a canonical field to a provider field, optionally through a transform. Dots address
nested objects.

## Adjust a tenant

```python
config = {
    "base_url": "https://erp.example.com",
    "auth": {"type": "bearer", "token": "..."},
    "entity_overrides": {
        "customers": {
            "fields": {"tax_id": "custom_vat"},
            "create_defaults": {"customer_group": "Retail"},
        },
        "payments": {"fields": {"reference": "ref"}},  # Odoo before 18
    },
    "custom_entities": {"tickets": {"path": "Issue", "key_fields": ["name"]}},
}
```

## Transforms

Built in: `identity`, `date`, `compact_date`, `odata_v2_date`, `odata_v2_datetime`, `decimal`,
`decimal_string`, `alpha10`, `alpha18`, `strip`, `relation`, `int_id`.

```python
from py_erp_bridge import Transform, register_transform

register_transform("cents", Transform(lambda v: v / 100, lambda v: int(v * 100)))
```

`read` converts provider to canonical, `write` the reverse, `filter` converts filter literals.

`record.raw` and `item.raw` keep the provider's data unchanged. A value that does not fit the model raises
`MappingError`, naming the field but not the value.
