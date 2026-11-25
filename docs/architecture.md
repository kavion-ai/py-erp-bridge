# Introduction and architecture

`py-erp-bridge` gives applications one typed interface to ten ERP systems. An
application works with canonical models (`Customer`, `Invoice`, ...); an adapter per provider
translates them. The original payload always stays available in `record.raw`.

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| Public SDK | `py_erp_bridge`, `client` | Sync and async clients, tenants |
| Core | `core` | Adapter contract, queries, mapping, bulk, sync, registry |
| Providers | `providers` | One adapter per ERP |
| Authentication | `auth` | Strategies and token handling |
| Transport | `transport` | HTTP, OData, XML/SOAP, RFC |
| Resilience | `resilience` | Retry, circuit breaker, rate limits |
| Models | `models` | Canonical records |
| Configuration | `config` | Validated settings |
| Observability | `observability` | Logging and metrics |
| Webhooks | `webhooks` | Verification and dispatch |
| Exceptions | `exceptions` | Error hierarchy |

The core never refers to a provider, so an eleventh one needs no change in it.

## System

```mermaid
flowchart TB
    App[Application] --> Sync[ERPClient]
    App --> Async[AsyncERPClient]
    Sync -->|private event loop| Async
    Async --> Res[Entity resources]
    Res --> Map[Entity mapper]
    Res --> Adapter[Provider adapter]
    Adapter --> HTTP[HTTP transport]
    Adapter --> RFC[RFC transport]
    HTTP --> Auth[Auth strategy]
    HTTP --> Resil[Retry, circuit breaker, rate limit]
    HTTP --> Obs[Logs and metrics]
    HTTP --> ERP[(ERP system)]
    RFC --> ERP
```

## Provider adapters

```mermaid
classDiagram
    class ERPAdapter {
        <<abstract>>
        +info
        +config_model
        +default_entities
        +list_page()
        +get()
        +create()
        +update()
        +delete()
        +bulk_create()
        +health_check()
        +execute()
    }
    ERPAdapter <|-- ODataAdapter
    ODataAdapter <|-- SapS4HanaAdapter
    ODataAdapter <|-- Dynamics365Adapter
    ERPAdapter <|-- SapEccAdapter
    ERPAdapter <|-- OracleFusionAdapter
    ERPAdapter <|-- NetSuiteAdapter
    ERPAdapter <|-- OdooAdapter
    ERPAdapter <|-- ERPNextAdapter
    ERPAdapter <|-- TallyAdapter
    ERPAdapter <|-- InforAdapter
    ERPAdapter <|-- SageIntacctAdapter
    ERPAdapter <|-- CustomAdapter
    ProviderRegistry --> ERPAdapter : loads lazily
```

## Authentication

```mermaid
sequenceDiagram
    participant T as HTTP transport
    participant S as OAuth 2.0 strategy
    participant I as Token endpoint
    participant E as ERP
    T->>S: apply(request)
    alt no valid token
        S->>I: grant or refresh (one at a time)
        I-->>S: token
        S->>S: save to token store
    end
    S-->>T: Authorization header
    T->>E: request
    E-->>T: 401
    T->>S: refresh(rejected request)
    S->>I: new token, unless already replaced
    T->>E: request, once more
    E-->>T: response
```

## Request and response

```mermaid
sequenceDiagram
    participant A as Application
    participant R as Entity resource
    participant M as Mapper
    participant P as Adapter
    participant T as Transport
    A->>R: create(data)
    R->>R: operation supported?
    R->>M: validate and map
    M-->>R: provider payload
    R->>P: create(spec, payload)
    P->>T: request
    T-->>P: response or typed error
    P-->>R: raw record
    R->>M: map back
    M-->>A: canonical record with raw
```

## Retry and errors

```mermaid
flowchart TD
    S[Attempt] --> C{Circuit open?}
    C -->|yes| X[CircuitOpenError]
    C -->|no| Q[Send]
    Q --> O{Outcome}
    O -->|success| D[Return]
    O -->|4xx| F[Typed error]
    O -->|429| W
    O -->|never sent| W
    O -->|5xx, 408 or lost reply| I{Idempotent?}
    I -->|no| F
    I -->|yes| W{Attempts left?}
    W -->|no| F
    W -->|yes| B[Retry-After or backoff with jitter] --> S
```

## Synchronization

```mermaid
flowchart LR
    L[Load watermark] --> Q[List modified since watermark minus overlap]
    Q --> H[Handle each record]
    H -->|all handled| W[Save newest timestamp]
    H -->|handler fails| K[Keep old watermark]
    K -.->|next run repeats| L
    W -.->|next run| L
```

## Decisions

- **One async implementation.** The blocking client runs it on a private event loop thread.
  There is no second code path to keep in step, and it works inside notebooks.
- **Mappings are data.** `EntitySpec` describes paths, keys and fields, so a tenant can adjust
  them in configuration.
- **Refuse rather than guess.** A field, filter or sort that a provider cannot express raises an
  error. An unexpected response body is never read as an empty list.
- **Evidence is recorded.** Every mapping states how well it is backed; see
  [capabilities](capabilities.md).
