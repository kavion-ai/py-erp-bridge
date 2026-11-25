# Installation and configuration

```bash
pip install py-erp-bridge
pip install "py-erp-bridge[jwt]"   # NetSuite machine-to-machine OAuth
```

Python 3.11 or newer. SAP RFC needs extra software: [SAP RFC](sap-rfc.md).

## Settings

Configuration is a dictionary or the provider's config class; both are validated.

```python
from py_erp_bridge import ERPClient

erp = ERPClient(
    "erpnext",
    {
        "base_url": "https://erp.example.com",
        "auth": {"type": "api_key", "prefix": "token ", "key": "KEY:SECRET"},
        "timeout": {"connect": 10, "read": 60},
        "retry": {"max_attempts": 4, "base_delay": 0.5, "max_delay": 30},
        "circuit_breaker": {"failure_threshold": 5, "reset_timeout": 30},
        "rate_limit": {"requests_per_second": 5, "burst": 5, "max_concurrency": 10},
        "pool": {"max_connections": 20, "max_keepalive": 10},
        "tls": {"ca_bundle": "/etc/ssl/corp.pem", "client_cert": "c.pem", "client_key": "k.pem"},
        "proxy": "http://proxy.example.com:3128",
        "headers": {"X-App": "billing"},
        "page_size": 100,
    },
)
```

`http://` is refused for anything but loopback unless `allow_insecure_http` is set.

## Environment variables

```python
from py_erp_bridge import config_from_env

config = config_from_env("ODOO")  # ODOO_BASE_URL, ODOO_AUTH__TYPE, ODOO_AUTH__TOKEN, ...
```

See `.env.example`. The SDK reads the process environment only, not `.env` files.

## Several tenants

```python
from py_erp_bridge import TenantManager

tenants = TenantManager({"acme": ("odoo", acme_config), "globex": ("netsuite", globex_config)})
tenants.get("acme").customers.get_all()
tenants.close()
```

Each tenant has its own connections, tokens, limits and circuit breaker.
