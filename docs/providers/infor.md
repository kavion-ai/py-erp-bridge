# Infor CloudSuite

Provider name: `infor`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/infor.py`](../../examples/infor.py).

## What was verified

Read: the meaning of the .ionapi fields (Infor documentation) and the service account grant (Infor's
ION API SDK). The M3 `m3api-rest/v2/execute` format was seen in third-party guides only, so requests,
responses and transactions are unconfirmed; try them in the ION API Swagger UI first.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

In Infor OS open *API Gateway > Authorized Apps*, create a *Backend Service* app and download its credentials with a service account.

## Permissions

The service account's user needs M3 authority for each MI program.

## Credentials to collect

The `.ionapi` file.

## Find the endpoint

`iu` and `ti` in the `.ionapi` file.

## Configure

```python
from py_erp_bridge.providers.infor import load_ionapi

config = load_ionapi("backend.ionapi")  # dict; add e.g. config["cono"] = 100
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("infor", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Customers support list, get, create and update; vendors, items and orders are read-only. Filters are
equality on the list transaction's inputs. A list longer than `max_records` raises an error instead of
being truncated. Deletion is not mapped.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('mi', program=..., transaction=..., record={...})` runs any MI transaction. CloudSuites built on LN or SyteLine have other APIs: use `client.request()`.
