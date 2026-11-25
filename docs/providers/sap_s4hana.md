# SAP S/4HANA

Provider name: `sap_s4hana`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/sap_s4hana.py`](../../examples/sap_s4hana.py).

## What was verified

Read: SAP's CSRF documentation through search, and SAP tutorials naming communication
scenarios SAP_COM_0008 (Business Partner) and SAP_COM_0109 (Sales Order). SAP Business
Accelerator Hub could not be read (it needs JavaScript), so service names, entity sets and
fields are unconfirmed except `API_SALES_ORDER_SRV/A_SalesOrder` (third-party sources).

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

**Cloud (public edition).** In *Maintain Communication Users* create a user. In *Communication
Systems* create an inbound system and attach the user. In *Communication Arrangements* create
one arrangement per API group, for example SAP_COM_0008 for business partners and SAP_COM_0109
for sales orders. If a scenario is missing, its scope item may be inactive.

**On premise / private cloud.** Activate the OData services in `/IWFND/MAINT_SERVICE` and create a
technical user.

## Permissions

Cloud: permissions come from the communication arrangement. On premise: `S_SERVICE` for the OData
services plus the business authorizations of the documents.

## Credentials to collect

Communication user and password, or OAuth client id, secret and token URL; on premise also the SAP client.

## Find the endpoint

The *Communication Arrangement* shows the API host (`https://myXXXXXX-api.s4hana.ondemand.com`). On premise use the gateway host.

## Configure

```python
{
    "base_url": "https://myXXXXXX-api.s4hana.ondemand.com",
    "sap_client": "100",  # on premise only
    "auth": {"type": "basic", "username": "...", "password": "..."},
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("sap_s4hana", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Customers and suppliers are read and updated directly but created through
`API_BUSINESS_PARTNER/A_BusinessPartner`; the example shows how. Order items are embedded.
CSRF tokens and session cookies are handled for you. Updates send `If-Match: *` unless you pass
`etag=`, so pass one to detect concurrent changes.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('create_atomic', ...)` writes one OData changeset; `bulk_create` uses `$batch` with independent changesets; `execute('soap', ...)` calls a SOAP service.
