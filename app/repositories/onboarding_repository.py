from __future__ import annotations

from typing import Any

from google.cloud import bigquery


class OnboardingRepository:
    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required")

        if not dataset_id:
            raise ValueError("dataset_id is required")

        self.project_id = project_id
        self.dataset_id = dataset_id

        self.client = client or bigquery.Client(
            project=project_id
        )

        self.organizations_table = (
            f"{project_id}.{dataset_id}.AI_ORGANIZATIONS"
        )

        self.users_table = (
            f"{project_id}.{dataset_id}.AI_USERS"
        )

        self.customers_table = (
            f"{project_id}.{dataset_id}.CUSTOMERS"
)

        self.ai_users_table = (
            f"{project_id}.{dataset_id}.AI_USERS"
        )

        self.subscriptions_table = (
            f"{project_id}.{dataset_id}.AI_SUBSCRIPTIONS"
                )
        
        self.subscriptions_table = (
                    f"{project_id}.{dataset_id}.CUSTOMER_SUBSCRIPTIONS"
        )

        # Commercial / SaaS tenant tables used by authentication,
        # billing, connections, and customer-user membership.
        self.customers_table = (
            f"{project_id}.{dataset_id}.CUSTOMERS"
        )

        self.customer_users_table = (
            f"{project_id}.{dataset_id}.CUSTOMER_USERS"
        )

        self.customer_subscriptions_table = (
            f"{project_id}.{dataset_id}.CUSTOMER_SUBSCRIPTIONS"
        )

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
                "tenant-isolated onboarding operations."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_customer_id(
        customer_id: str,
    ) -> str:
        normalized = str(
            customer_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "customer_id is required for "
                "commercial tenant provisioning."
            )

        if not normalized.startswith("cust_"):
            raise ValueError(
                "customer_id must use the cust_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_user_id(
        user_id: str,
    ) -> str:
        normalized = str(
            user_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "user_id is required for "
                "commercial tenant provisioning."
            )

        if not normalized.startswith("user_"):
            raise ValueError(
                "user_id must use the user_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_email(
        email: str,
    ) -> str:
        normalized = str(email or "").strip().lower()

        if not normalized:
            raise ValueError(
                "email is required for onboarding operations."
            )

        return normalized

    def get_user_by_email(
        self,
        *,
        email: str,
        organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_email = self._require_email(
            email
        )

        query_parameters: list[
            bigquery.ScalarQueryParameter
        ] = [
            bigquery.ScalarQueryParameter(
                "email",
                "STRING",
                normalized_email,
            ),
        ]

        tenant_filter = ""

        if organization_id is not None:
            effective_organization_id = (
                self._require_organization_id(
                    organization_id
                )
            )

            tenant_filter = (
                "AND organization_id = @organization_id"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                )
            )

        query = f"""
        SELECT
            user_id,
            organization_id,
            email,
            full_name,
            role,
            auth_provider,
            last_login,
            is_active,
            created_at,
            updated_at
        FROM `{self.ai_users_table}`
        WHERE LOWER(email) = @email
        AND is_active = TRUE
        {tenant_filter}
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=query_parameters
        )

        rows = list(
            self.client.query(
                query,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        return dict(rows[0].items())

    def get_organization_by_user_email(
        self,
        *,
        email: str,
        organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Resolve organization membership for an authenticated email.

        organization_id is optional only to support first-login/onboarding
        resolution. When tenant context is already known, callers should
        pass it so the lookup fails closed to that organization.
        """

        normalized_email = self._require_email(
            email
        )

        query_parameters: list[
            bigquery.ScalarQueryParameter
        ] = [
            bigquery.ScalarQueryParameter(
                "email",
                "STRING",
                normalized_email,
            ),
        ]

        tenant_filter = ""

        if organization_id is not None:
            effective_organization_id = (
                self._require_organization_id(
                    organization_id
                )
            )

            tenant_filter = (
                "AND customer.organization_id = "
                "@organization_id"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                )
            )

        query = f"""
        SELECT
            customer.customer_id,
            customer.organization_id,
            customer.organization_name,
            customer.organization_slug,
            customer.owner_user_id AS user_id,
            customer.owner_email AS email,
            customer.customer_status,
            customer.default_plan_code,
            customer.onboarding_status,
            customer.is_active,
            customer.created_at,
            customer.updated_at,

            ai_user.role AS user_role

        FROM `{self.customers_table}` AS customer

        LEFT JOIN `{self.ai_users_table}` AS ai_user
            ON ai_user.organization_id =
            customer.organization_id
        AND LOWER(ai_user.email) = @email
        AND ai_user.is_active = TRUE

        WHERE customer.is_active = TRUE
        AND (
            LOWER(customer.owner_email) = @email
            OR ai_user.email IS NOT NULL
        )
        {tenant_filter}

        ORDER BY
            customer.updated_at DESC,
            customer.created_at DESC

        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=query_parameters
        )

        rows = list(
            self.client.query(
                query,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        row = dict(rows[0].items())

        resolved_organization_id = str(
            row.get("organization_id") or ""
        ).strip()

        if (
            organization_id is not None
            and resolved_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Resolved organization does not match "
                "the authenticated organization."
            )

        return row

    def get_active_subscription(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
            subscription_record_id,
            customer_id,
            organization_id,
            subscription_status,
            plan_code,
            billing_interval,
            trial_started_at,
            trial_ends_at,
            subscription_started_at,
            current_period_started_at,
            current_period_ends_at,
            cancel_at_period_end,
            canceled_at,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_price_id,
            currency,
            unit_amount_cents,
            seat_limit,
            connection_limit,
            monthly_explanation_limit,
            monthly_dq_analysis_limit,
            payment_status,
            is_current,
            created_at,
            updated_at
        FROM `{self.subscriptions_table}`
        WHERE organization_id = @organization_id
        AND is_current = TRUE
        AND UPPER(subscription_status) IN (
            'ACTIVE',
            'TRIAL',
            'TRIALING'
        )
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = list(
            self.client.query(
                query,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        row = dict(rows[0].items())

        # Map new commercial subscription fields
        # to the shape OnboardingService already expects.
        return {
            "subscription_id": row.get(
                "subscription_record_id"
            ),
            "organization_id": row.get(
                "organization_id"
            ),
            "plan_name": row.get("plan_code"),
            "billing_status": (
                "TRIAL"
                if str(
                    row.get("subscription_status") or ""
                ).upper()
                in {"TRIAL", "TRIALING"}
                else row.get("subscription_status")
            ),
            "stripe_customer_id": row.get(
                "stripe_customer_id"
            ),
            "stripe_subscription_id": row.get(
                "stripe_subscription_id"
            ),
            "trial_end_date": row.get(
                "trial_ends_at"
            ),
            "renewal_date": row.get(
                "current_period_ends_at"
            ),
            "max_users": row.get("seat_limit"),
            "max_connections": row.get(
                "connection_limit"
            ),
            "max_monthly_explanations": row.get(
                "monthly_explanation_limit"
            ),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def create_organization(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                str(
                    record.get("organization_id")
                    or ""
                )
            )
        )

        query = f"""
        INSERT INTO `{self.organizations_table}`
        (
            organization_id,
            organization_name,
            company_domain,
            subscription_plan,
            subscription_status,
            trial_start_date,
            trial_end_date,
            max_users,
            max_connections,
            industry,
            company_size,
            country,
            timezone,
            created_by,
            created_at,
            updated_at,
            is_active
        )
        VALUES
        (
            @organization_id,
            @organization_name,
            @company_domain,
            @subscription_plan,
            @subscription_status,
            @trial_start_date,
            @trial_end_date,
            @max_users,
            @max_connections,
            @industry,
            @company_size,
            @country,
            @timezone,
            @created_by,
            @created_at,
            @updated_at,
            @is_active
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_name",
                    "STRING",
                    record["organization_name"],
                ),
                bigquery.ScalarQueryParameter(
                    "company_domain",
                    "STRING",
                    record.get("company_domain"),
                ),
                bigquery.ScalarQueryParameter(
                    "subscription_plan",
                    "STRING",
                    record.get(
                        "subscription_plan"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "subscription_status",
                    "STRING",
                    record.get(
                        "subscription_status"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "trial_start_date",
                    "TIMESTAMP",
                    record.get(
                        "trial_start_date"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "trial_end_date",
                    "TIMESTAMP",
                    record.get("trial_end_date"),
                ),
                bigquery.ScalarQueryParameter(
                    "max_users",
                    "INT64",
                    record.get("max_users"),
                ),
                bigquery.ScalarQueryParameter(
                    "max_connections",
                    "INT64",
                    record.get(
                        "max_connections"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "industry",
                    "STRING",
                    record.get("industry"),
                ),
                bigquery.ScalarQueryParameter(
                    "company_size",
                    "STRING",
                    record.get("company_size"),
                ),
                bigquery.ScalarQueryParameter(
                    "country",
                    "STRING",
                    record.get("country"),
                ),
                bigquery.ScalarQueryParameter(
                    "timezone",
                    "STRING",
                    record.get("timezone"),
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    record.get("created_by"),
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    record["created_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    record["updated_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "is_active",
                    "BOOL",
                    record.get("is_active", True),
                ),
            ]
        )

        query_job = self.client.query(
            query,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Organization insert did not affect "
                "exactly one row."
            )

        saved_record = dict(record)
        saved_record["organization_id"] = (
            organization_id
        )

        return saved_record

    def create_user(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                str(
                    record.get("organization_id")
                    or ""
                )
            )
        )

        normalized_email = self._require_email(
            str(record.get("email") or "")
        )

        # INSERT ... SELECT ensures users can only be created beneath
        # an existing active organization.
        query = f"""
        INSERT INTO `{self.users_table}`
        (
            user_id,
            organization_id,
            email,
            full_name,
            role,
            auth_provider,
            last_login,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            @user_id,
            @organization_id,
            @email,
            @full_name,
            @role,
            @auth_provider,
            @last_login,
            @is_active,
            @created_at,
            @updated_at
        FROM (SELECT 1) AS tenant_guard
        WHERE EXISTS (
            SELECT 1
            FROM `{self.organizations_table}`
            WHERE organization_id = @organization_id
              AND is_active = TRUE
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "user_id",
                    "STRING",
                    record["user_id"],
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                ),
                bigquery.ScalarQueryParameter(
                    "full_name",
                    "STRING",
                    record.get("full_name"),
                ),
                bigquery.ScalarQueryParameter(
                    "role",
                    "STRING",
                    record.get("role", "OWNER"),
                ),
                bigquery.ScalarQueryParameter(
                    "auth_provider",
                    "STRING",
                    record.get(
                        "auth_provider",
                        "GOOGLE",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "last_login",
                    "TIMESTAMP",
                    record.get("last_login"),
                ),
                bigquery.ScalarQueryParameter(
                    "is_active",
                    "BOOL",
                    record.get("is_active", True),
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    record["created_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    record["updated_at"],
                ),
            ]
        )

        query_job = self.client.query(
            query,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "User was not created for the "
                "requested organization."
            )

        saved_record = dict(record)
        saved_record["organization_id"] = (
            organization_id
        )
        saved_record["email"] = normalized_email

        return saved_record

    def create_subscription(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                str(
                    record.get("organization_id")
                    or ""
                )
            )
        )

        # INSERT ... SELECT ensures subscriptions can only be created
        # beneath an existing active organization.
        query = f"""
        INSERT INTO `{self.subscriptions_table}`
        (
            subscription_id,
            organization_id,
            plan_name,
            billing_status,
            stripe_customer_id,
            stripe_subscription_id,
            trial_end_date,
            renewal_date,
            max_users,
            max_connections,
            max_monthly_explanations,
            created_at,
            updated_at
        )
        SELECT
            @subscription_id,
            @organization_id,
            @plan_name,
            @billing_status,
            @stripe_customer_id,
            @stripe_subscription_id,
            @trial_end_date,
            @renewal_date,
            @max_users,
            @max_connections,
            @max_monthly_explanations,
            @created_at,
            @updated_at
        FROM (SELECT 1) AS tenant_guard
        WHERE EXISTS (
            SELECT 1
            FROM `{self.organizations_table}`
            WHERE organization_id = @organization_id
              AND is_active = TRUE
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "subscription_id",
                    "STRING",
                    record["subscription_id"],
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "plan_name",
                    "STRING",
                    record["plan_name"],
                ),
                bigquery.ScalarQueryParameter(
                    "billing_status",
                    "STRING",
                    record["billing_status"],
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_customer_id",
                    "STRING",
                    record.get(
                        "stripe_customer_id"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_subscription_id",
                    "STRING",
                    record.get(
                        "stripe_subscription_id"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "trial_end_date",
                    "TIMESTAMP",
                    record.get("trial_end_date"),
                ),
                bigquery.ScalarQueryParameter(
                    "renewal_date",
                    "TIMESTAMP",
                    record.get("renewal_date"),
                ),
                bigquery.ScalarQueryParameter(
                    "max_users",
                    "INT64",
                    record.get("max_users"),
                ),
                bigquery.ScalarQueryParameter(
                    "max_connections",
                    "INT64",
                    record.get(
                        "max_connections"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "max_monthly_explanations",
                    "INT64",
                    record.get(
                        "max_monthly_explanations"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    record["created_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    record["updated_at"],
                ),
            ]
        )

        query_job = self.client.query(
            query,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Subscription was not created for "
                "the requested organization."
            )

        saved_record = dict(record)
        saved_record["organization_id"] = (
            organization_id
        )

        return saved_record

    def provision_commercial_tenant(
        self,
        *,
        customer_id: str,
        organization_id: str,
        organization_name: str,
        organization_slug: str | None,
        user_id: str,
        owner_email: str,
        display_name: str | None,
        plan_code: str,
        trial_started_at: Any,
        trial_ends_at: Any,
        seat_limit: int | None,
        connection_limit: int | None,
        monthly_explanation_limit: int | None,
        monthly_dq_analysis_limit: int | None = None,
        created_by: str,
    ) -> dict[str, Any]:
        """
        Provision the commercial SaaS tenant records required by auth,
        billing, connections, and user membership.

        This method deliberately requires customer_id, organization_id,
        and user_id. It never derives or accepts tenant identity from a
        browser-controlled payload.

        BigQuery MERGE statements make retries idempotent while preserving
        the same tenant keys across CUSTOMERS, CUSTOMER_USERS, and
        CUSTOMER_SUBSCRIPTIONS.
        """
        effective_customer_id = (
            self._require_customer_id(
                customer_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        effective_user_id = self._require_user_id(
            user_id
        )
        effective_email = self._require_email(
            owner_email
        )
        effective_created_by = self._require_email(
            created_by
        )

        normalized_organization_name = str(
            organization_name or ""
        ).strip()

        if not normalized_organization_name:
            raise ValueError(
                "organization_name is required for "
                "commercial tenant provisioning."
            )

        normalized_plan_code = str(
            plan_code or ""
        ).strip().upper()

        if not normalized_plan_code:
            raise ValueError(
                "plan_code is required for "
                "commercial tenant provisioning."
            )

        subscription_record_id = (
            f"subrec_{effective_customer_id.removeprefix('cust_')}"
        )
        customer_user_id = (
            f"cu_{effective_user_id.removeprefix('user_')}"
        )

        sql = f"""
        BEGIN TRANSACTION;

        MERGE `{self.customers_table}` AS target
        USING (
            SELECT
                @customer_id AS customer_id,
                @organization_id AS organization_id
        ) AS source
        ON target.customer_id = source.customer_id
           AND target.organization_id = source.organization_id
        WHEN NOT MATCHED THEN
          INSERT (
              customer_id,
              organization_id,
              organization_name,
              organization_slug,
              owner_user_id,
              owner_email,
              billing_email,
              customer_status,
              customer_type,
              default_plan_code,
              default_environment,
              trial_eligible,
              onboarding_status,
              onboarding_completed_at,
              is_active,
              created_at,
              created_by,
              updated_at,
              updated_by
          )
          VALUES (
              @customer_id,
              @organization_id,
              @organization_name,
              @organization_slug,
              @user_id,
              @owner_email,
              @owner_email,
              'ACTIVE',
              'ENTERPRISE',
              @plan_code,
              'PRODUCTION',
              TRUE,
              'COMPLETE',
              CURRENT_TIMESTAMP(),
              TRUE,
              CURRENT_TIMESTAMP(),
              @created_by,
              CURRENT_TIMESTAMP(),
              @created_by
          )
        WHEN MATCHED THEN
          UPDATE SET
              organization_name = @organization_name,
              organization_slug = COALESCE(
                  @organization_slug,
                  target.organization_slug
              ),
              owner_user_id = @user_id,
              owner_email = @owner_email,
              billing_email = COALESCE(
                  target.billing_email,
                  @owner_email
              ),
              default_plan_code = @plan_code,
              onboarding_status = 'COMPLETE',
              onboarding_completed_at = COALESCE(
                  target.onboarding_completed_at,
                  CURRENT_TIMESTAMP()
              ),
              is_active = TRUE,
              updated_at = CURRENT_TIMESTAMP(),
              updated_by = @created_by;

        MERGE `{self.customer_users_table}` AS target
        USING (
            SELECT
                @customer_id AS customer_id,
                @organization_id AS organization_id,
                @user_id AS user_id,
                @owner_email AS email
        ) AS source
        ON target.organization_id = source.organization_id
           AND LOWER(target.email) = LOWER(source.email)
        WHEN NOT MATCHED THEN
          INSERT (
              customer_user_id,
              customer_id,
              organization_id,
              user_id,
              email,
              display_name,
              organization_role,
              billing_role,
              invitation_status,
              accepted_at,
              is_active,
              created_at,
              created_by,
              updated_at
          )
          VALUES (
              @customer_user_id,
              @customer_id,
              @organization_id,
              @user_id,
              @owner_email,
              @display_name,
              'OWNER',
              'BILLING_ADMIN',
              'ACCEPTED',
              CURRENT_TIMESTAMP(),
              TRUE,
              CURRENT_TIMESTAMP(),
              @created_by,
              CURRENT_TIMESTAMP()
          )
        WHEN MATCHED THEN
          UPDATE SET
              customer_id = @customer_id,
              user_id = @user_id,
              display_name = COALESCE(
                  @display_name,
                  target.display_name
              ),
              organization_role = 'OWNER',
              billing_role = 'BILLING_ADMIN',
              invitation_status = 'ACCEPTED',
              accepted_at = COALESCE(
                  target.accepted_at,
                  CURRENT_TIMESTAMP()
              ),
              is_active = TRUE,
              updated_at = CURRENT_TIMESTAMP();

        MERGE `{self.customer_subscriptions_table}` AS target
        USING (
            SELECT
                @subscription_record_id
                    AS subscription_record_id,
                @customer_id AS customer_id,
                @organization_id AS organization_id
        ) AS source
        ON target.customer_id = source.customer_id
           AND target.organization_id = source.organization_id
           AND target.is_current = TRUE
        WHEN NOT MATCHED THEN
          INSERT (
              subscription_record_id,
              customer_id,
              organization_id,
              subscription_status,
              plan_code,
              billing_interval,
              trial_started_at,
              trial_ends_at,
              currency,
              unit_amount_cents,
              seat_limit,
              connection_limit,
              monthly_explanation_limit,
              monthly_dq_analysis_limit,
              payment_status,
              is_current,
              created_at,
              created_by,
              updated_at,
              updated_by
          )
          VALUES (
              @subscription_record_id,
              @customer_id,
              @organization_id,
              'TRIALING',
              @plan_code,
              'MONTH',
              @trial_started_at,
              @trial_ends_at,
              'USD',
              NULL,
              @seat_limit,
              @connection_limit,
              @monthly_explanation_limit,
              @monthly_dq_analysis_limit,
              'NOT_REQUIRED',
              TRUE,
              CURRENT_TIMESTAMP(),
              @created_by,
              CURRENT_TIMESTAMP(),
              @created_by
          )
        WHEN MATCHED THEN
          UPDATE SET
              plan_code = @plan_code,
              trial_started_at = COALESCE(
                  target.trial_started_at,
                  @trial_started_at
              ),
              trial_ends_at = COALESCE(
                  target.trial_ends_at,
                  @trial_ends_at
              ),
              seat_limit = COALESCE(
                  @seat_limit,
                  target.seat_limit
              ),
              connection_limit = COALESCE(
                  @connection_limit,
                  target.connection_limit
              ),
              monthly_explanation_limit = COALESCE(
                  @monthly_explanation_limit,
                  target.monthly_explanation_limit
              ),
              monthly_dq_analysis_limit = COALESCE(
                  @monthly_dq_analysis_limit,
                  target.monthly_dq_analysis_limit
              ),
              updated_at = CURRENT_TIMESTAMP(),
              updated_by = @created_by;

        COMMIT TRANSACTION;
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_id",
                    "STRING",
                    effective_customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_name",
                    "STRING",
                    normalized_organization_name,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_slug",
                    "STRING",
                    (
                        str(organization_slug).strip()
                        if organization_slug
                        else None
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "user_id",
                    "STRING",
                    effective_user_id,
                ),
                bigquery.ScalarQueryParameter(
                    "owner_email",
                    "STRING",
                    effective_email,
                ),
                bigquery.ScalarQueryParameter(
                    "display_name",
                    "STRING",
                    (
                        str(display_name).strip()
                        if display_name
                        else None
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "plan_code",
                    "STRING",
                    normalized_plan_code,
                ),
                bigquery.ScalarQueryParameter(
                    "trial_started_at",
                    "TIMESTAMP",
                    trial_started_at,
                ),
                bigquery.ScalarQueryParameter(
                    "trial_ends_at",
                    "TIMESTAMP",
                    trial_ends_at,
                ),
                bigquery.ScalarQueryParameter(
                    "seat_limit",
                    "INT64",
                    seat_limit,
                ),
                bigquery.ScalarQueryParameter(
                    "connection_limit",
                    "INT64",
                    connection_limit,
                ),
                bigquery.ScalarQueryParameter(
                    "monthly_explanation_limit",
                    "INT64",
                    monthly_explanation_limit,
                ),
                bigquery.ScalarQueryParameter(
                    "monthly_dq_analysis_limit",
                    "INT64",
                    monthly_dq_analysis_limit,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    effective_created_by,
                ),
                bigquery.ScalarQueryParameter(
                    "customer_user_id",
                    "STRING",
                    customer_user_id,
                ),
                bigquery.ScalarQueryParameter(
                    "subscription_record_id",
                    "STRING",
                    subscription_record_id,
                ),
            ]
        )

        self.client.query(
            sql,
            job_config=job_config,
        ).result()

        return {
            "customer_id": effective_customer_id,
            "organization_id": effective_organization_id,
            "user_id": effective_user_id,
            "owner_email": effective_email,
            "subscription_record_id": subscription_record_id,
            "customer_user_id": customer_user_id,
        }

