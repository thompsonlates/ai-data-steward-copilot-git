from __future__ import annotations

from google.cloud import bigquery


class RecordSearchService:
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
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for tenant-isolated record search."
            )

        return normalized

    def search_records(
        self,
        *,
        organization_id: str,
        domain: str,
        search_text: str,
        limit: int = 20,
    ) -> list[bigquery.table.Row]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        normalized_domain = str(domain or "").strip()
        normalized_search_text = str(search_text or "").strip()

        if not normalized_domain:
            raise ValueError("domain is required")

        if not normalized_search_text:
            raise ValueError("search_text is required")

        safe_limit = max(1, min(int(limit), 100))

        sql = """
        SELECT
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
            phone_number
        FROM `api-project-503305938314.ai_data_steward_mvp.MDM_RECORD_SEARCH_INDEX`
        WHERE organization_id = @organization_id
          AND UPPER(domain) = UPPER(@domain)
          AND LOWER(search_text) LIKE LOWER(CONCAT('%', @search_text, '%'))
        ORDER BY display_name, record_id
        LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
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
                    "search_text",
                    "STRING",
                    normalized_search_text,
                ),
                bigquery.ScalarQueryParameter(
                    "limit",
                    "INT64",
                    safe_limit,
                ),
            ]
        )

        return list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )
