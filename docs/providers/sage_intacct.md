# Sage Intacct

Provider name: `sage_intacct`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/sage_intacct.py`](../../examples/sage_intacct.py).

## What was verified

Sage's developer site refused automated access. From search results: base URL, token endpoint, client
credentials with `username`, and the query service's restrictions. The query format, object names and
fields are unconfirmed; check them in Sage's OpenAPI definition.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

Register an application in the Sage developer portal. In *Company > Company Info > Security* add its client id to the authorized client applications.

## Permissions

A Web Services user with permissions on each application.

## Credentials to collect

Client id and secret; the Web Services user as `user@company` (some sources add `|entity`).

## Find the endpoint

`https://api.intacct.com/ia/api/v1` (the default).

## Configure

```python
{
    "auth": {
        "type": "oauth2_client_credentials",
        "token_url": "https://api.intacct.com/ia/api/v1/oauth2/token",
        "client_id": "...",
        "client_secret": "...",
        "extra_params": {"username": "user@company"},
    }
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("sage_intacct", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Records are addressed by their system `key`; the visible id is in `raw["id"]`. Lists return mapped fields
only, `get` returns everything. Payments are read-only.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('query', body={...})` sends a native query.
