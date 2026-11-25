# Odoo

Provider name: `odoo`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/odoo.py`](../../examples/odoo.py).

## What was verified

Read: Odoo 19's external API page (headers, errors, transactions, key expiry, plans). The argument
names `vals_list`, `vals` and `ids` come from third-party clients; confirm them on your database's
`/doc` page. Sources disagree on when XML-RPC and JSON-RPC go away: the current page says Odoo 22
(fall 2028) and Odoo Online 21.1, an older documentation commit said Odoo 20. Use JSON-2 where you can.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

The external API needs a Custom pricing plan. Create a dedicated integration user.

## Permissions

Groups of that user, per application (Sales, Purchase, Accounting, ...).

## Credentials to collect

*Preferences > Account Security > New API Key*. Keys last at most three months, so plan their rotation.

## Find the endpoint

Your database URL. Set `database` if one host serves several.

## Configure

```python
{"base_url": "https://COMPANY.odoo.com", "auth": {"type": "bearer", "token": "API_KEY"}}

# Before Odoo 19
{
    "base_url": "https://odoo.example.com",
    "api": "jsonrpc",
    "database": "prod",
    "auth": {"type": "basic", "username": "login", "password": "API_KEY"},
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("odoo", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Relations are read and written as ids: `country` is a country id, for instance. Order and invoice
items can be written; updating them replaces all lines. `bulk_create` is one transaction.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('call', model=..., method=..., params={...})` reaches every public method, e.g. `action_confirm`.
