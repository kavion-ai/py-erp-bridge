# SAP ECC

Provider name: `sap_ecc`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/sap_ecc.py`](../../examples/sap_ecc.py).

## What was verified

Read: the PyRFC documentation and its archive notice. Tables, BAPIs and `RFC_READ_TABLE` behaviour
are established conventions that could not be confirmed in SAP documentation; check SE11/SE37.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

Create a technical user of type *System* or *Communications* (SU01). Make the application server's gateway port (33NN) reachable from your host. Nothing needs to be deployed unless you replace the table reader.

## Permissions

`S_RFC` for the function groups you call, `S_TABU_NAM`/`S_TABU_DIS` for the tables you read, and the business authorizations of the BAPIs.

## Credentials to collect

Application server host and system number (or message server, system id and logon group), client, user and password, or SNC parameters.

## Find the endpoint

SAP GUI logon entry: application server and instance number.

## Configure

```python
{
    "rfc": {
        "ashost": "sap.example.com",
        "sysnr": "00",
        "client": "100",
        "user": "...",
        "passwd": "...",
    }
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("sap_ecc", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Reads use `RFC_READ_TABLE`; sorting is not available. Sales and purchase orders are created
by passing BAPI parameters in `provider_fields`; canonical fields are refused rather than
half-translated. Updates and deletes go through `execute('call_bapi', ...)`, which commits on
success and rolls back if `RETURN` contains an error.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

See [SAP RFC](../sap-rfc.md) for the runtime, and for replacing `RFC_READ_TABLE`.
