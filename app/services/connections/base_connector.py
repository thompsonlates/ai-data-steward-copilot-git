from abc import ABC, abstractmethod
from typing import Any


class BaseEnterpriseConnector(ABC):
    """
    Common read-oriented contract for governed enterprise data connectors.

    Implementations remain responsible for vendor-specific connectivity,
    authentication, SQL/dialect behavior, and identifier safety.

    Profiling, DQ evaluation, AI recommendations, and governed remediation
    do not belong in this base connector.
    """

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Validate connectivity using the configured tenant-bound credentials."""
        raise NotImplementedError

    @abstractmethod
    def list_schemas(self) -> list[str]:
        """Return schemas visible to the configured connection."""
        raise NotImplementedError

    @abstractmethod
    def list_tables(
        self,
        *,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return tables visible to the configured connection."""
        raise NotImplementedError

    @abstractmethod
    def get_columns(
        self,
        *,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """Return column metadata for a governed table."""
        raise NotImplementedError

    @abstractmethod
    def read_sample(
        self,
        *,
        schema: str,
        table: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read a bounded sample from a governed table."""
        raise NotImplementedError