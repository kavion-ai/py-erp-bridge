"""Built-in providers. Adapters are imported only when used."""

from py_erp_bridge.core.registry import ProviderRegistry

registry = ProviderRegistry()

for _name, _class in {
    "sap_s4hana": "SapS4HanaAdapter",
    "sap_ecc": "SapEccAdapter",
    "oracle_fusion": "OracleFusionAdapter",
    "netsuite": "NetSuiteAdapter",
    "dynamics365": "Dynamics365Adapter",
    "odoo": "OdooAdapter",
    "erpnext": "ERPNextAdapter",
    "tally": "TallyAdapter",
    "infor": "InforAdapter",
    "sage_intacct": "SageIntacctAdapter",
}.items():
    registry.register(_name, f"py_erp_bridge.providers.{_name}:{_class}")

__all__ = ["registry"]
