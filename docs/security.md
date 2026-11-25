# Security best practices

Built in:

- TLS is verified; plain http is refused for remote hosts; redirects are not followed.
- Credentials go only to the origin of `base_url` and the configured token endpoint.
- Secrets are `SecretStr`; configuration errors do not echo values; bodies and queries are not logged.
- XML from the network is parsed without DTDs and entities.
- Filter values, keys and XML are escaped or rejected.
- Webhook signatures are compared in constant time.

Your part:

- Keep secrets in a vault or the environment, never in code. Rotate them.
- Use a dedicated integration user with least privilege per application and tenant.
- Never expose TallyPrime's port: it has no authentication.
- `request()`, `execute()` and SOAP bodies are powerful: do not pass untrusted input to them.
- `APIError.body` and `record.raw` can contain personal data; do not log them blindly.
- `tls.verify = False` is for local debugging only.
- Keep `Filter.field` names out of reach of untrusted users: names are not escaped, values are.
