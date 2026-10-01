from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery


class RecordSearchRepository:
    TABLE_ID = (
        "api-project-503305938314."
        "ai_data_steward_mvp."
        "MDM_RECORD_SEARCH_INDEX"
    )

    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        self.client = client or bigquery.Client()

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated record indexing."
            )

        return normalized

    def upsert_record(
        self,
        *,
        organization_id: str,
        domain: str,
        record: dict[str, Any],
        created_by: str,
        record_origin: str = "MANUAL_ENTRY",
    ) -> None:

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        normalized_domain = str(
            domain or ""
        ).strip().upper()

        if not normalized_domain:
            raise ValueError(
                "domain is required."
            )

        record_id = self._resolve_record_id(
            domain=normalized_domain,
            record=record,
        )

        if not record_id:
            # Do not pollute the search index with
            # records that have no stable searchable ID.
            return

        source_system = str(
            record.get("source_system")
            or record_origin
        ).strip()

        display_name = self._build_display_name(
            domain=normalized_domain,
            record=record,
        )

        search_text = self._build_search_text(
            domain=normalized_domain,
            record=record,
            display_name=display_name,
            record_id=record_id,
        )

        now = datetime.now(timezone.utc)

        sql = f"""
        MERGE `{self.TABLE_ID}` AS target

        USING (
            SELECT
                @organization_id AS organization_id,
                @domain AS domain,
                @record_id AS record_id,
                @source_system AS source_system
        ) AS source

        ON target.organization_id = source.organization_id
           AND UPPER(target.domain) = UPPER(source.domain)
           AND target.record_id = source.record_id
           AND COALESCE(target.source_system, '') =
               COALESCE(source.source_system, '')

        WHEN MATCHED THEN
          UPDATE SET
            display_name = @display_name,
            search_text = @search_text,

            mdm_id = @mdm_id,
            member_id = @member_id,
            patient_id = @patient_id,
            provider_id = @provider_id,
            supplier_id = @supplier_id,
            product_id = @product_id,

            npi = @npi,
            tax_id = @tax_id,
            specialty = @specialty,
            gtin = @gtin,
            dob = @dob,
            sku = @sku,

            product_name = @product_name,
            item_category = @item_category,
            product_variant = @product_variant,
            effective_lot_date = @effective_lot_date,

            supplier_name = @supplier_name,
            supplier_name_line_1 =
                @supplier_name_line_1,
            supplier_name_line_2 =
                @supplier_name_line_2,

            first_name = @first_name,
            last_name = @last_name,
            email = @email,
            address = @address,
            human_id = @human_id,
            phone_number = @phone_number,

            organization_entity_id = @organization_entity_id,
            organization_code = @organization_code,
            organization_name = @organization_name,
            organization_type = @organization_type,
            parent_organization_id = @parent_organization_id,
            organization_status = @organization_status,

            location_id = @location_id,
            site_id = @site_id,
            location_code = @location_code,
            location_name = @location_name,
            location_type = @location_type,
            location_address = @location_address,
            parent_location_id = @parent_location_id,
            updated_at = @updated_at

        WHEN NOT MATCHED THEN
          INSERT (
            organization_id,
            record_id,
            mdm_id,
            domain,
            display_name,
            source_system,
            golden_record_flag,
            member_id,
            patient_id,
            provider_id,
            supplier_id,
            product_id,
            npi,
            tax_id,
            specialty,
            gtin,
            dob,
            sku,
            product_name,
            item_category,
            product_variant,
            effective_lot_date,
            supplier_name,
            supplier_name_line_1,
            supplier_name_line_2,
            first_name,
            last_name,
            email,
            address,
            human_id,
            phone_number,
            organization_entity_id,
            organization_code,
            organization_name,
            organization_type,
            parent_organization_id,
            organization_status,
            location_id,
            site_id,
            location_code,
            location_name,
            location_type,
            location_address,
            parent_location_id,
            search_text,
            record_origin,
            created_by,
            created_at,
            updated_at
          )
            VALUES (
            @organization_id,
            @record_id,
            @mdm_id,
            @domain,
            @display_name,
            @source_system,
            FALSE,
            @member_id,
            @patient_id,
            @provider_id,
            @supplier_id,
            @product_id,
            @npi,
            @tax_id,
            @specialty,
            @gtin,
            @dob,
            @sku,
            @product_name,
            @item_category,
            @product_variant,
            @effective_lot_date,
            @supplier_name,
            @supplier_name_line_1,
            @supplier_name_line_2,
            @first_name,
            @last_name,
            @email,
            @address,
            @human_id,
            @phone_number,

            @organization_entity_id,
            @organization_code,
            @organization_name,
            @organization_type,
            @parent_organization_id,
            @organization_status,

            @location_id,
            @site_id,
            @location_code,
            @location_name,
            @location_type,
            @location_address,
            @parent_location_id,

            @search_text,
            @record_origin,
            @created_by,
            @created_at,
            @updated_at
          )
        """

        params = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                effective_organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "domain",
                "STRING",
                normalized_domain,
            ),
            bigquery.ScalarQueryParameter(
                "record_id",
                "STRING",
                record_id,
            ),
            bigquery.ScalarQueryParameter(
                "source_system",
                "STRING",
                source_system,
            ),
            bigquery.ScalarQueryParameter(
                "display_name",
                "STRING",
                display_name,
            ),
            bigquery.ScalarQueryParameter(
                "search_text",
                "STRING",
                search_text,
            ),
            bigquery.ScalarQueryParameter(
                "record_origin",
                "STRING",
                record_origin,
            ),
            bigquery.ScalarQueryParameter(
                "created_by",
                "STRING",
                str(created_by or "").strip(),
            ),
            bigquery.ScalarQueryParameter(
                "created_at",
                "TIMESTAMP",
                now,
            ),
            bigquery.ScalarQueryParameter(
                "updated_at",
                "TIMESTAMP",
                now,
            ),
        ]

        for field_name in (
            "mdm_id",
            "member_id",
            "patient_id",
            "provider_id",
            "supplier_id",
            "product_id",
            "npi",
            "tax_id",
            "specialty",
            "gtin",
            "dob",
            "sku",
            "product_name",
            "item_category",
            "product_variant",
            "effective_lot_date",
            "supplier_name",
            "supplier_name_line_1",
            "supplier_name_line_2",
            "first_name",
            "last_name",
            "email",
            "address",
            "human_id",
            "phone_number",
            "organization_entity_id",
            "organization_code",
            "organization_name",
            "organization_type",
            "parent_organization_id",
            "organization_status",
            "location_id",
            "site_id",
            "location_code",
            "location_name",
            "location_type",
            "location_address",
            "parent_location_id",
        ):
            params.append(
                bigquery.ScalarQueryParameter(
                    field_name,
                    "STRING",
                    self._string_value(
                        record.get(field_name)
                    ),
                )
            )

        job_config = bigquery.QueryJobConfig(
            query_parameters=params
        )

        self.client.query(
            sql,
            job_config=job_config,
        ).result()

    @staticmethod
    def _string_value(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()

        return normalized or None

    @staticmethod
    def _resolve_record_id(
        *,
        domain: str,
        record: dict[str, Any],
    ) -> str | None:

        candidates_by_domain = {
            "CUSTOMER": [
                "member_id",
                "customer_id",
            ],
            "PATIENT": [
                "patient_id",
                "member_id",
                "human_id",
            ],
            "PROVIDER": [
                "provider_id",
                "npi",
            ],
            "SUPPLIER": [
                "supplier_id",
                "tax_id",
            ],
            "PRODUCT": [
                "product_id",
                "gtin",
                "sku",
            ],
            "ORGANIZATION": [
                "organization_entity_id",
                "organization_code",
            ],

            "LOCATION": [
                "location_id",
                "site_id",
                "location_code",
            ],
        }

        candidates = candidates_by_domain.get(
            domain,
            ["member_id", "record_id"],
        )

        for field_name in candidates:
            value = str(
                record.get(field_name) or ""
            ).strip()

            if value:
                return value

        return None

    @staticmethod
    def _build_display_name(
        *,
        domain: str,
        record: dict[str, Any],
    ) -> str:

        if domain == "PRODUCT":
            return str(
                record.get("product_name")
                or record.get("product_id")
                or "Product"
            ).strip()

        if domain == "SUPPLIER":
            return str(
                record.get("supplier_name")
                or record.get(
                    "supplier_name_line_1"
                )
                or record.get("supplier_id")
                or "Supplier"
            ).strip()

        if domain == "ORGANIZATION":
            return str(
                record.get("organization_name")
                or record.get("organization_code")
                or record.get("organization_entity_id")
                or "Organization"
            ).strip()

        if domain == "LOCATION":
            return str(
                record.get("location_name")
                or record.get("location_code")
                or record.get("site_id")
                or record.get("location_id")
                or "Location"
            ).strip()

        first_name = str(
            record.get("first_name")
            or record.get("provider_first_name")
            or record.get("patient_first_name")
            or ""
        ).strip()

        last_name = str(
            record.get("last_name")
            or record.get("provider_last_name")
            or record.get("patient_last_name")
            or ""
        ).strip()

        full_name = (
            f"{first_name} {last_name}"
        ).strip()

        return full_name or str(
            record.get("provider_id")
            or record.get("patient_id")
            or record.get("member_id")
            or "Record"
        ).strip()

    @staticmethod
    def _build_search_text(
        *,
        domain: str,
        record: dict[str, Any],
        display_name: str,
        record_id: str,
    ) -> str:

        values = [
            domain,
            record_id,
            display_name,
        ]

        searchable_fields = (
            "mdm_id",
            "member_id",
            "patient_id",
            "provider_id",
            "supplier_id",
            "product_id",
            "npi",
            "tax_id",
            "gtin",
            "sku",
            "first_name",
            "last_name",
            "provider_first_name",
            "provider_last_name",
            "patient_first_name",
            "patient_last_name",
            "supplier_name",
            "product_name",
            "email",
            "provider_email",
            "patient_email",
            "address",
            "provider_address",
            "patient_address",
            "source_system",
            "human_id",
            "phone_number",
            # Organization
            "organization_entity_id",
            "organization_code",
            "organization_name",
            "organization_type",
            "parent_organization_id",
            "organization_status",
            # Location
            "location_id",
            "site_id",
            "location_code",
            "location_name",
            "location_type",
            "location_address",
            "parent_location_id",
        )

        for field_name in searchable_fields:
            value = str(
                record.get(field_name) or ""
            ).strip()

            if value:
                values.append(value)

        return " ".join(
            value
            for value in values
            if value
        )