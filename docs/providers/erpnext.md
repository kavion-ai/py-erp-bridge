# ERPNext

Provider name: `erpnext`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/erpnext.py`](../../examples/erpnext.py).

## What was verified

Read in Frappe's documentation: REST resources, filters, authentication, file upload and webhook
signature (`X-Frappe-Webhook-Signature`, base64 HMAC-SHA256). DocType fields, and the fields of the
Webhook DocType, are unconfirmed; check your site's DocTypes.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

Create an integration user. Nothing else is needed.

## Permissions

Roles with permissions on each DocType.

## Credentials to collect

*User > Settings > API Access > Generate Keys*: API key and secret.

## Find the endpoint

The site URL.

## Configure

```python
{
    "base_url": "https://SITE",
    "auth": {"type": "api_key", "prefix": "token ", "key": "API_KEY:API_SECRET"},
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("erpnext", config) as erp:
    print(erp.test_connection())
```

## Read and write records

All documents support CRUD except Bin and GL Entry. Lists omit items; `get` includes them. Created
documents are drafts until `execute('submit', entity=..., key=...)`; cancel before deleting.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

File upload, webhook registration and `execute('call_method', ...)` are supported.
