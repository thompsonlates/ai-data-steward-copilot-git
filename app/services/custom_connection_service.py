from __future__ import annotations

import logging
import uuid

from app.api.schemas import (
    CustomConnectionRequestCreate,
)

from app.repositories.custom_connection_repository import (
    CustomConnectionRepository,
)


logger = logging.getLogger(__name__)


class CustomConnectionService:
    def __init__(
        self,
        *,
        repository: CustomConnectionRepository,
        email_service=None,
    ) -> None:
        self.repository = repository
        self.email_service = email_service

    def create_request(
        self,
        *,
        request: CustomConnectionRequestCreate,
        organization_id: str,
        customer_id: str,
        requested_by: str,
        contact_email: str,
    ) -> dict:

        effective_organization_id = str(
            organization_id or ""
        ).strip()

        effective_customer_id = str(
            customer_id or ""
        ).strip()

        effective_requested_by = str(
            requested_by or ""
        ).strip().lower()

        effective_contact_email = str(
            contact_email or ""
        ).strip().lower()

        if not effective_organization_id:
            raise ValueError(
                "organization_id is required."
            )

        if not effective_organization_id.startswith(
            "org_"
        ):
            raise ValueError(
                "organization_id must use the "
                "org_ identifier standard."
            )

        if not effective_customer_id:
            raise ValueError(
                "customer_id is required."
            )

        if not effective_requested_by:
            raise ValueError(
                "requested_by is required."
            )

        if not effective_contact_email:
            raise ValueError(
                "contact_email is required."
            )

        vendor_name = str(
            request.vendor_name or ""
        ).strip()

        connection_type = str(
            request.connection_type or ""
        ).strip().upper()

        environment = str(
            request.environment or ""
        ).strip().upper()

        auth_preference = (
            str(request.auth_preference).strip()
            if request.auth_preference
            else None
        )

        use_case = (
            str(request.use_case).strip()
            if request.use_case
            else None
        )

        if not vendor_name:
            raise ValueError(
                "vendor_name is required."
            )

        if not connection_type:
            raise ValueError(
                "connection_type is required."
            )

        if not environment:
            raise ValueError(
                "environment is required."
            )

        request_id = (
            f"ccr_{uuid.uuid4().hex[:24]}"
        )

        logger.info(
            "Creating custom connection request. "
            "request_id=%s organization_id=%s "
            "customer_id=%s vendor=%s "
            "connection_type=%s requested_by=%s",
            request_id,
            effective_organization_id,
            effective_customer_id,
            vendor_name,
            connection_type,
            effective_requested_by,
        )

        result = self.repository.create_request(
            request_id=request_id,
            organization_id=(
                effective_organization_id
            ),

            customer_id=effective_customer_id,
            requested_by=effective_requested_by,
            contact_email=effective_contact_email,
            vendor_name=vendor_name,
            connection_type=connection_type,
            environment=environment,
            auth_preference=auth_preference,
            read_data_required=bool(
                request.read_data_required
            ),
            metadata_required=bool(
                request.metadata_required
            ),
            use_case=use_case,
            request_status="REQUESTED",
        )

        if self.email_service is not None:
            try:
                self.email_service.send_custom_connection_request_notification(
                    request_id=result["request_id"],
                    organization_id=result["organization_id"],
                    customer_id=result["customer_id"],
                    requested_by=result["requested_by"],
                    contact_email=result["contact_email"],
                    vendor_name=result["vendor_name"],
                    connection_type=result["connection_type"],
                    environment=result["environment"],
                    auth_preference=result.get(
                        "auth_preference"
                    ),
                    read_data_required=result[
                        "read_data_required"
                    ],
                    metadata_required=result[
                        "metadata_required"
                    ],
                    use_case=result.get("use_case"),
                )

            except Exception as email_exc:
                logger.exception(
                    "Custom connection request saved, "
                    "but Postmark notification failed. "
                    "request_id=%s error_type=%s",
                    result.get("request_id"),
                    type(email_exc).__name__,
                )

        logger.info(
            "Custom connection request created. "
            "request_id=%s organization_id=%s "
            "vendor=%s",
            request_id,
            effective_organization_id,
            vendor_name,
        )

        return result