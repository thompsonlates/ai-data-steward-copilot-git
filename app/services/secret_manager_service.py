from __future__ import annotations

import json
import re
from typing import Any


from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import secretmanager


class SecretManagerService:
    def __init__(self, project_id: str) -> None:
        if not project_id:
            raise ValueError("project_id is required")

        self.project_id = project_id
        self.client = secretmanager.SecretManagerServiceClient()

    @staticmethod
    def sanitize_secret_id(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_-]", "-", value.strip())
        normalized = re.sub(r"-+", "-", normalized).strip("-")

        if not normalized:
            raise ValueError("Unable to generate a valid secret ID")

        return normalized[:255]

    def get_secret_payload(
        self,
        credential_reference: str,
    ) -> dict[str, Any]:
        response = self.client.access_secret_version(
            request={"name": credential_reference}
        )

        payload = response.payload.data.decode("UTF-8")

        return json.loads(payload)

    def build_secret_id(
        self,
        connection_id: str,
        environment: str,
    ) -> str:
        return self.sanitize_secret_id(
            f"enterprise-connection-{environment}-{connection_id}"
        ).lower()

    def update_secret_by_reference(
        self,
        credential_reference: str,
        credential_payload: dict[str, Any],
    ) -> str:
        secret_version_name = self._reference_to_resource_name(
            credential_reference
        )

        secret_name = secret_version_name.rsplit(
            "/versions/",
            1,
        )[0]

        payload = json.dumps(
            credential_payload
        ).encode("utf-8")

        self.client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {
                    "data": payload,
                },
            }
        )

        return f"secretmanager://{secret_name}/versions/latest"

    def create_or_update_secret(
        self,
        secret_id: str,
        credential_payload: dict[str, Any],
    ) -> str:
        """
        Creates the secret container when necessary and always adds a new version.

        Returns:
            Secret reference suitable for BigQuery storage.
        """
        parent = f"projects/{self.project_id}"
        secret_name = f"{parent}/secrets/{secret_id}"

        try:
            self.client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {
                        "replication": {
                            "automatic": {}
                        }
                    },
                }
            )
        except AlreadyExists:
            pass

        payload = json.dumps(credential_payload).encode("utf-8")

        self.client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {
                    "data": payload
                },
            }
        )

        return f"secretmanager://{secret_name}/versions/latest"

    def access_secret(self, credential_reference: str) -> dict[str, Any]:
        secret_version_name = self._reference_to_resource_name(
            credential_reference
        )

        response = self.client.access_secret_version(
            request={"name": secret_version_name}
        )

        raw_payload = response.payload.data.decode("utf-8")
        return json.loads(raw_payload)

    def delete_secret(self, credential_reference: str) -> None:
        secret_version_name = self._reference_to_resource_name(
            credential_reference
        )

        secret_name = secret_version_name.rsplit("/versions/", 1)[0]

        try:
            self.client.delete_secret(
                request={"name": secret_name}
            )
        except NotFound:
            return

    @staticmethod
    def _reference_to_resource_name(reference: str) -> str:
        prefix = "secretmanager://"

        if not reference.startswith(prefix):
            raise ValueError("Invalid Secret Manager reference")

        resource_name = reference.removeprefix(prefix)

        if "/versions/" not in resource_name:
            resource_name = f"{resource_name}/versions/latest"

        return resource_name