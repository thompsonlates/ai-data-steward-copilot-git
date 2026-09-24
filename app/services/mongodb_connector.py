from __future__ import annotations
import re
import logging
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:
    MongoClient = None  # type: ignore[assignment]

    class PyMongoError(Exception):
        pass


logger = logging.getLogger(__name__)


class MongoDBConnector:
    def __init__(
        self,
        *,
        organization_id: str,
        connection_id: str,
        connection_uri: str,
        database: str,
        timeout_ms: int = 10000,
    ) -> None:
        self.organization_id = self._require_organization_id(organization_id)
        self.connection_id = self._require_connection_id(connection_id)
        self.connection_uri = str(connection_uri or "").strip()
        self.database = str(database or "").strip()

        if not self.connection_uri:
            raise ValueError("MongoDB connection_uri is required.")
        if not self.database:
            raise ValueError("MongoDB database is required.")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be greater than zero.")

        self.timeout_ms = timeout_ms

    def test_connection(self) -> dict[str, Any]:
        client = self._create_client()
        try:
            client.admin.command("ping")
            logger.info(
                "MongoDB connection test succeeded. organization_id=%s connection_id=%s",
                self.organization_id,
                self.connection_id,
            )
            return {
                "status": "HEALTHY",
                "vendor": "MONGODB",
                "database": self.database,
            }
        except PyMongoError as exc:
            raise RuntimeError("Unable to connect to MongoDB.") from exc
        finally:
            client.close()

    def list_collections(self) -> list[str]:
        client = self._create_client()
        try:
            return sorted(client[self.database].list_collection_names())
        except PyMongoError as exc:
            raise RuntimeError("Unable to list MongoDB collections.") from exc
        finally:
            client.close()

    def read_sample(
        self,
        *,
        collection: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        collection_name = str(collection or "").strip()
        if not collection_name:
            raise ValueError("collection is required.")
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")

        client = self._create_client()
        try:
            cursor = client[self.database][collection_name].find({}, limit=limit)
            return [
                {
                    str(key): self._normalize_value(value)
                    for key, value in document.items()
                }
                for document in cursor
            ]
        except PyMongoError as exc:
            raise RuntimeError("Unable to read MongoDB sample records.") from exc
        finally:
            client.close()

    def _create_client(self) -> Any:
        if MongoClient is None:
            raise RuntimeError("MongoDB support requires the pymongo package.")

        return MongoClient(
            self.connection_uri,
            serverSelectionTimeoutMS=self.timeout_ms,
            connectTimeoutMS=self.timeout_ms,
            socketTimeoutMS=self.timeout_ms,
            retryReads=True,
        )

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): cls._normalize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._normalize_value(v) for v in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _require_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()
        if not normalized:
            raise ValueError("organization_id is required for tenant-isolated MongoDB operations.")
        if not normalized.startswith("org_"):
            raise ValueError("organization_id must use the org_ identifier standard.")
        return normalized

    @staticmethod
    def _require_connection_id(connection_id: str) -> str:
        normalized = str(connection_id or "").strip()

        if not normalized:
            raise ValueError(
                "connection_id is required for MongoDB operations."
            )

        is_conn_id = normalized.startswith("conn_")

        is_uuid = bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{12}",
                normalized,
            )
        )

        if not is_conn_id and not is_uuid:
            raise ValueError(
                "connection_id must use either the "
                "conn_ identifier standard or a valid UUID."
            )

        return normalized
