# Supported providers

| Provider | Name | Setup, credentials and permissions |
|---|---|---|
| SAP S/4HANA | `sap_s4hana` | [guide](providers/sap_s4hana.md) |
| SAP ECC | `sap_ecc` | [guide](providers/sap_ecc.md) |
| Oracle Fusion Cloud ERP | `oracle_fusion` | [guide](providers/oracle_fusion.md) |
| Oracle NetSuite | `netsuite` | [guide](providers/netsuite.md) |
| Microsoft Dynamics 365 F&O | `dynamics365` | [guide](providers/dynamics365.md) |
| Odoo | `odoo` | [guide](providers/odoo.md) |
| ERPNext | `erpnext` | [guide](providers/erpnext.md) |
| TallyPrime | `tally` | [guide](providers/tally.md) |
| Infor CloudSuite (M3) | `infor` | [guide](providers/infor.md) |
| Sage Intacct | `sage_intacct` | [guide](providers/sage_intacct.md) |

Operations, fields and limitations per provider: [capabilities](capabilities.md).

Every adapter is tested against mocked servers. None has been run against a live system, so start
in a sandbox: run `test_connection()`, read a few records, and compare `record.raw` with the mapped
fields. Correct differences with `entity_overrides`; see [mapping](mapping.md).
