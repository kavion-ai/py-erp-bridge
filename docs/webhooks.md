# Webhooks

```python
from pydantic import SecretStr
from py_erp_bridge.webhooks import WebhookProcessor, frappe_verifier

hooks = WebhookProcessor(
    frappe_verifier(SecretStr("secret")),
    event_type=lambda headers, payload: payload.get("doctype"),
    event_id=lambda headers, payload: f"{payload.get('name')}:{payload.get('modified')}",
)


@hooks.on("Customer")
async def changed(event):
    print(event.payload)


await hooks.process(headers, raw_body)  # in your web framework
```

Pass the raw body: re-encoded JSON does not match the signature. Duplicates return `None`. If a handler
raises, the claim is released so a redelivery is processed. Use a shared `DeduplicationStore` with
several workers.

| Provider | Events |
|---|---|
| ERPNext | Signed webhooks; `erp.register_webhook(...)` creates them |
| Odoo | Automation rules can call a URL; unsigned, so use `SharedSecretVerifier` or a secret URL |
| Dynamics 365 | Business events; no signature found in the documentation |
| NetSuite | SuiteScript or a SuiteApp |
| SAP S/4HANA | SAP Event Mesh |
| Oracle Fusion | Oracle Integration business events |
| Infor | ION BODs or Event Hub |
| SAP ECC, TallyPrime, Sage Intacct | Not implemented; poll with [incremental sync](sync.md) where a timestamp is mapped |

`HmacVerifier` covers other signed sources. See `examples/webhooks.py`.
