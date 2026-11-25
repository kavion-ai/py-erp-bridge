"""SAP RFC transport over a pyrfc-compatible connection.

SAP archived PyRFC, so it is neither bundled nor declared as a dependency. Any object with
``call``, ``ping`` and ``close`` can be supplied instead through ``set_connection_factory``.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from py_erp_bridge.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ERPConnectionError,
    MissingDependencyError,
)
from py_erp_bridge.types import JsonObject

T = TypeVar("T")


class RfcConfig(BaseModel):
    """Logon parameters, named as in the SAP NetWeaver RFC SDK."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client: str
    lang: str = "EN"
    user: str | None = None
    passwd: SecretStr | None = None
    ashost: str | None = None
    sysnr: str | None = None
    mshost: str | None = None
    msserv: str | None = None
    sysid: str | None = None
    group: str | None = None
    saprouter: str | None = None
    snc_qop: str | None = None
    snc_myname: str | None = None
    snc_partnername: str | None = None
    snc_lib: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> RfcConfig:
        direct = self.ashost and self.sysnr
        balanced = self.mshost and self.sysid and self.group
        if bool(direct) == bool(balanced):
            raise ValueError("set either ashost+sysnr or mshost+sysid+group")
        if not (self.user and self.passwd) and not self.snc_partnername:
            raise ValueError("set user+passwd or snc_* parameters")
        return self

    def parameters(self) -> dict[str, str]:
        """Connection parameters with secrets revealed."""
        values = self.model_dump(exclude_none=True)
        if self.passwd:
            values["passwd"] = self.passwd.get_secret_value()
        return cast("dict[str, str]", values)


class RfcConnection(Protocol):
    """The subset of ``pyrfc.Connection`` that is used."""

    def call(self, func_name: str, **params: Any) -> dict[str, Any]:  # noqa: ANN401
        """Call a remote-enabled function module."""

    def ping(self) -> object:
        """Check the connection."""

    def close(self) -> None:
        """Close the connection."""


ConnectionFactory = Callable[[dict[str, str]], RfcConnection]


def _pyrfc_factory(parameters: dict[str, str]) -> RfcConnection:
    try:
        import pyrfc
    except ImportError as exc:
        raise MissingDependencyError(
            "RFC needs a pyrfc-compatible module and the SAP NW RFC SDK; see docs/sap-rfc.md"
        ) from exc
    return cast("RfcConnection", pyrfc.Connection(**parameters))


_factory: ConnectionFactory = _pyrfc_factory


def set_connection_factory(factory: ConnectionFactory | None) -> None:
    """Use another RFC implementation; None restores pyrfc."""
    global _factory
    _factory = factory or _pyrfc_factory


class RfcTransport:
    """One connection, used by one call at a time (RFC connections are not thread-safe)."""

    def __init__(self, config: RfcConfig) -> None:
        self._config = config
        self._connection: RfcConnection | None = None
        self._lock = asyncio.Lock()

    async def call(self, function: str, **params: object) -> JsonObject:
        """Call a function module. Never retried: a lost reply may hide a success."""
        async with self._lock:
            return await self._call(function, **params)

    async def session(self, work: Callable[[RfcCall], Awaitable[T]]) -> T:
        """Run several calls in one ABAP session, without interleaving."""
        async with self._lock:
            return await work(self._call)

    async def ping(self) -> None:
        """Check the connection."""
        async with self._lock:
            connection = await self._connect()
            await self._invoke(connection.ping)

    async def aclose(self) -> None:
        """Close the connection."""
        async with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                await asyncio.to_thread(connection.close)

    async def _connect(self) -> RfcConnection:
        if self._connection is None:
            self._connection = await self._invoke(
                functools.partial(_factory, self._config.parameters())
            )
        return self._connection

    async def _call(self, function: str, **params: object) -> JsonObject:
        connection = await self._connect()
        return cast(
            "JsonObject", await self._invoke(functools.partial(connection.call, function, **params))
        )

    async def _invoke(self, work: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(work)
        except MissingDependencyError:
            raise
        except asyncio.CancelledError:
            # The thread is still using this connection; never hand it to another call.
            self._connection = None
            raise
        except Exception as exc:
            # Class names are matched so that no specific implementation is imported.
            names = {c.__name__ for c in type(exc).__mro__}
            if "LogonError" in names:
                self._connection = None
                raise AuthenticationError("SAP logon failed") from exc
            if "CommunicationError" in names:
                self._connection = None
                raise ERPConnectionError(f"RFC communication failure: {exc}") from exc
            if names & {"ABAPApplicationError", "ABAPRuntimeError"}:
                raise BusinessRuleError(
                    f"ABAP error: {exc}", provider="sap", code=getattr(exc, "key", None)
                ) from exc
            raise


class RfcCall(Protocol):
    """Signature of calls inside a session."""

    def __call__(self, function: str, **params: object) -> Awaitable[JsonObject]:
        """Call a function module."""
