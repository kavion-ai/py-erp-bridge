# SAP RFC

## Runtime

SAP archived PyRFC on 28 May 2026 and no longer maintains it; its README says that the SDK patch level it
needs is no longer supported. There is no official successor. The SDK therefore does not depend on
it and accepts any object with `call`, `ping` and `close`:

```python
from py_erp_bridge.transport.rfc import set_connection_factory

set_connection_factory(lambda parameters: MyConnection(**parameters))
```

Without a factory it tries `import pyrfc`. That needs the SAP NetWeaver RFC SDK, which requires an SAP
support login, on the library path. Assess any third-party replacement yourself before using it.

## Calls

```python
erp.execute("call", function="STFC_CONNECTION", params={"REQUTEXT": "hello"})
erp.execute("call_bapi", function="BAPI_PO_CHANGE", params={"PURCHASEORDER": "4500000001"})
```

`call_bapi` runs the BAPI and `BAPI_TRANSACTION_COMMIT` in one ABAP session. If `RETURN` contains a
type E, A or X message it rolls back and raises `BusinessRuleError`. RFC calls are never retried: a lost
reply may hide a success. One client uses one connection, one call at a time.

## Reading tables

Reads use `RFC_READ_TABLE`, which SAP has not released for customers. Rows are limited in width (commonly reported as 512
bytes), so select few fields on wide tables, and there is no guaranteed order between pages. For
production, deploy your own reader with the same interface and set `read_table_function`.

Numeric keys are padded (ALPHA conversion) for customers, vendors, materials and documents.
Filter lines are limited to 72 characters.
