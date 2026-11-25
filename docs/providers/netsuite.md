# Oracle NetSuite

Provider name: `netsuite`. Status: tested against mocks only, **not against a live system**.
Entities, fields, evidence and limitations: [capabilities](../capabilities.md). Runnable code:
[`examples/netsuite.py`](../../examples/netsuite.py).

## What was verified

Read in Oracle's documentation: the token request and JWT claims, record create, update and delete
(204 with `Location`), collection filtering and paging, SuiteQL, error format, authentication
options. Field ids beyond the customer example are unconfirmed.

Menu paths below can differ between releases; the vendor's documentation is authoritative.

## Enable API access and create the integration

*Setup > Company > Enable Features > SuiteCloud*: enable REST Web Services, SuiteAnalytics Workbook and OAuth 2.0. *Setup > Integration > Manage Integrations*: create an integration with Client Credentials (Machine to Machine) and the REST Web Services scope.

## Permissions

A role with *Log in using OAuth 2.0 Access Tokens*, *REST Web Services*, and permissions on each record type. Mapping the certificate needs *OAuth 2.0 Authorized Applications Management*.

## Credentials to collect

Client id; a key pair whose certificate you upload under *Setup > Integration > Manage Authentication > OAuth 2.0 Client Credentials Setup*; the resulting certificate id (`key_id`). A sandbox refresh clears this mapping.

## Find the endpoint

*Setup > Company > Company Information > Company URLs*: the SuiteTalk URL.

## Configure

```python
{
    "base_url": "https://ACCOUNT.suitetalk.api.netsuite.com",
    "auth": {
        "type": "oauth2_jwt_assertion",
        "token_url": "https://ACCOUNT.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token",
        "client_id": "...",
        "key_id": "...",
        "private_key": "-----BEGIN PRIVATE KEY-----...",
        "scopes": ["rest_webservices"],
    },
}
```

## Test the connection

```python
from py_erp_bridge import ERPClient

with ERPClient("netsuite", config) as erp:
    print(erp.test_connection())
```

## Read and write records

Install the `jwt` extra. Lists fetch each record separately; set `expand_lists=False` for ids only,
or use SuiteQL. `create` returns only the id; pass `refresh=True` for the record. Keys may be
`eid:<external id>`. The default concurrency is 4.

```python
for customer in erp.customers.iterate(limit=100):
    print(customer.id, customer.name, customer.raw)

customer = erp.customers.get("1001")
print(erp.capabilities()["customers"])  # supported operations
```

## Provider-specific features

`execute('suiteql', query=..., limit=..., offset=...)`. Token-based authentication (`oauth1`) is also supported.
