# Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ConfigurationError: ... does not support auth type` | Use a type listed in [capabilities](capabilities.md) |
| `base_url uses plain http` | Use https, or `allow_insecure_http` on a trusted network |
| `InvalidCredentialsError` | Expired key (Odoo: three months), wrong tenant, cleared NetSuite sandbox mapping |
| `PermissionDeniedError` | Missing role, or inactive communication arrangement |
| `NotFoundError` on every call | Wrong resource name; confirm it in the ERP's metadata and override `path` |
| `response contains no record list` | A login page, a proxy, or another API version answered |
| `response is not a JSON object` | Same |
| `MappingError: ... unexpected data` | The mapping does not fit your system; compare `raw`, then override |
| `refusing to send credentials to a different origin` | Absolute URL of another host in `request()` |
| `CircuitOpenError` | The ERP failed repeatedly; wait `reset_timeout` |
| `invalid_grant` from NetSuite | Clock skew, wrong `key_id`, or algorithm not matching the key |
| Empty Dynamics lists | Wrong `company`; try `cross_company` |
| Tally does not answer | Company not open, or server port off |
| `MissingDependencyError` | Install the `jwt` extra, or an [RFC runtime](sap-rfc.md) |

`configure_logging("INFO")` prints one line per attempt with a `request_id`, also sent as `X-Request-ID`.
