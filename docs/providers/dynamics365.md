# Microsoft Dynamics 365 Finance & Operations

Provider name: `dynamics365`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/dynamics365.py`](../../examples/dynamics365.py).

## What was verified

Read in Microsoft's documentation: OData options and filter operators, cross-company, batches,
paging limit, throttling, app registration, and the entity pages of Customers V3, Sales order headers
V2, Released products V2 and Purchase order headers V2. Collection names for the latter two, and the
other entities, are unconfirmed.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

Register an application in Microsoft Entra ID with the *Dynamics ERP* API. In Finance & Operations open *System administration > Setup > Microsoft Entra applications* and map its client id to a dedicated user.

## Permissions

Security roles of the mapped user. Data entities must be public.

## Credentials to collect

Tenant id, client id, client secret.

## Find the endpoint

The environment URL in Lifecycle Services or the Power Platform admin center.

## Configure

```python
{
    "base_url": "https://ORG.operations.dynamics.com",
    "company": "usmf",
    "auth": {
        "type": "oauth2_client_credentials",
        "token_url": "https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/token",
        "scope": "https://ORG.operations.dynamics.com/.default",
        "client_id": "...",
        "client_secret": "...",
    },
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("dynamics365", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Ids are `company/account`; with `company` configured the account alone is enough. Headers do not
embed lines: write them as a custom entity (`SalesOrderLinesV2`). Use
`execute('list_entities')` to confirm names.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('create_atomic', ...)` writes one changeset. Business Central and Dataverse are different APIs and are not covered.
