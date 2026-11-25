# Oracle Fusion Cloud ERP

Provider name: `oracle_fusion`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/oracle_fusion.py`](../../examples/oracle_fusion.py).

## What was verified

Oracle's REST reference redirected to a landing page, so it could not be read. The URL pattern,
`describe`, basic authentication, paging by `limit`/`offset`, `q` filters, `hasMore` and the 500
record limit come from Oracle quick starts and search results. Resource names and attributes are
unconfirmed (suppliers and purchaseOrders: third-party sources).

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

REST services are on by default. For OAuth, register a confidential application in the identity domain (IAM/IDCS) of your Fusion instance with access to the Fusion resource.

## Permissions

Assign the integration user job or custom roles holding the REST privileges and data access of each resource.

## Credentials to collect

User name and password, or client id, secret, scope and token URL of the identity domain.

## Find the endpoint

The host of your Fusion URL, for example `https://xxxx.fa.ocs.oraclecloud.com`.

## Configure

```python
{
    "base_url": "https://xxxx.fa.ocs.oraclecloud.com",
    "auth": {"type": "basic", "username": "...", "password": "..."},
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("oracle_fusion", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Record ids are taken from each record's self link, because some resources use hashed keys.
Run `execute('describe', entity='vendors')` first: it lists which attributes exist, and which can be
queried. Deletes are not mapped.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('soap', ...)` calls SOAP services. FBDI file loads are not implemented.
