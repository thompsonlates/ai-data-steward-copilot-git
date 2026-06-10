from google.cloud import bigquery

class RecordSearchService:

    def __init__(self):
        self.client = bigquery.Client()

    def search_records(
        self,
        domain: str,
        search_text: str,
        limit: int = 20
    ):

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
            sku,
            product_name,
            product_variant,
            effective_lot_date,
            first_name,
            last_name,
            email,
            address
            FROM `api-project-503305938314.ai_data_steward_mvp.MDM_RECORD_SEARCH_INDEX`
            WHERE UPPER(domain) = UPPER(@domain)
            AND LOWER(search_text) LIKE LOWER(CONCAT('%', @search_text, '%'))
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    domain,
                ),
                bigquery.ScalarQueryParameter(
                    "search_text",
                    "STRING",
                    search_text,
                ),
                bigquery.ScalarQueryParameter(
                    "limit",
                    "INT64",
                    limit,
                ),
            ]
        )

        return list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )