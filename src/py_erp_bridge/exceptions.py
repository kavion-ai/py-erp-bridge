"""Exception hierarchy. Everything the SDK raises on purpose derives from UniversalERPError."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from py_erp_bridge.core.bulk import BulkResult


class UniversalERPError(Exception):
    """Base class of all SDK errors."""


class ConfigurationError(UniversalERPError):
    """The client was configured incorrectly."""


class ProviderNotFoundError(ConfigurationError):
    """No adapter is registered under the requested name."""


class MissingDependencyError(ConfigurationError):
    """An optional dependency is required for the requested feature."""


class UnsupportedOperationError(UniversalERPError):
    """The provider (or this adapter) does not support the requested operation."""


class MappingError(UniversalERPError):
    """Data could not be converted between canonical and provider form."""


class PayloadValidationError(UniversalERPError):
    """Caller supplied data that does not match the canonical model."""


class TransportError(UniversalERPError):
    """The request failed before a usable response was received."""

    def __init__(self, message: str, *, request_sent: bool = True) -> None:
        super().__init__(message)
        #: False only when the request certainly never reached the server.
        self.request_sent = request_sent


class ERPConnectionError(TransportError):
    """Connection could not be established or was dropped."""


class ERPTimeoutError(TransportError):
    """A connect, read, write or pool timeout elapsed."""


class CircuitOpenError(UniversalERPError):
    """The circuit breaker is open; the request was not sent."""


class AuthenticationError(UniversalERPError):
    """Credentials were rejected or a token could not be obtained."""


class APIError(UniversalERPError):
    """The ERP returned an error response."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        #: Truncated response body, for diagnostics. May contain business data; do not log blindly.
        self.body = body


class InvalidCredentialsError(APIError, AuthenticationError):
    """HTTP 401."""


class PermissionDeniedError(APIError):
    """HTTP 403."""


class NotFoundError(APIError):
    """HTTP 404 / 410 or a provider-level missing record."""


class ConflictError(APIError):
    """HTTP 409, 412 or 428: duplicate record or concurrent modification."""


class RequestRejectedError(APIError):
    """HTTP 400 / 422 or another 4xx: the ERP rejected the request."""


class BusinessRuleError(APIError):
    """The ERP accepted the call but reported a business-level failure."""


class RateLimitError(APIError):
    """HTTP 429."""


class ServerError(APIError):
    """HTTP 5xx."""


class PartialFailureError(UniversalERPError):
    """Some items of a bulk operation failed; inspect ``result``."""

    def __init__(self, message: str, result: BulkResult) -> None:
        super().__init__(message)
        self.result = result


class WebhookError(UniversalERPError):
    """A webhook could not be processed."""


class SignatureVerificationError(WebhookError):
    """A webhook signature is missing or wrong."""
