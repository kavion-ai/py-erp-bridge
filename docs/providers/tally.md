# TallyPrime

Provider name: `tally`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/tally.py`](../../examples/tally.py).

## What was verified

Read in Tally's documentation: ports, envelopes, static variables, ledger create and alter, vouchers,
import results. Voucher collections (`Vouchers : VoucherType`, `CHILDOF`, `BELONGSTO`) are unconfirmed.
JSON (TallyPrime 7.0+) is not implemented; XML works on all releases.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

Open the company in TallyPrime. Let Tally act as a server on port 9000: *F1 > Settings > Advanced Configuration*, or *Exchange > Data Synchronization* (the documentation names both).

## Permissions

None: whoever reaches the port can read and write. Keep it on localhost or behind a VPN and an authenticating proxy.

## Credentials to collect

None. `basic` auth is available for a proxy in front of Tally.

## Find the endpoint

`http://<host>:9000`. Set `allow_insecure_http` for a remote host only on a trusted network.

## Configure

```python
{"base_url": "http://localhost:9000", "company": "Acme Ltd"}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("tally", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Masters (customers, vendors, products) support list, get, create and update; the name is the key.
Every list reads the whole collection and filters locally. Limit vouchers with
`params={"from_date": "20240401", "to_date": "20250331"}`. Deletion is not implemented.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('import_voucher', ...)` imports a voucher from native fields after checking that entries balance.
