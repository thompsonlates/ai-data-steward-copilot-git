"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get(
    "/metrics/governance-overview",
    response_model=GovernanceOverviewResponse,
)
def get_governance_overview(
    days: int = Query(30, ge=1, le=365),
    current_user: AuthUser = Depends(get_current_user),
):
    project_id = "api-project-503305938314"
    dataset_id = "ai_data_steward_mvp"
    organization_id = require_current_organization_id(
        current_user
    )

    ai_feedback_metrics = (
        metrics.get_ai_recommendation_feedback_metrics(
            organization_id=organization_id,
            days=days,
        )
    )

    # --------------------------------------------------
    # Latest deterministic DQ profile evidence
    # --------------------------------------------------
    # Governance AI uses this only when the dataset domain matches
    # the latest DQ profile domain. The governance service engine
    # enforces that boundary so PRODUCT evidence can never be
    # attached to a SUPPLIER governance dataset, etc.
    latest_dq_profile_run_id: str | None = None
    latest_dq_domain: str | None = None

    try:
        dq_overview = metrics.get_dq_dashboard_overview(
            days=days,
            domain=None,
            organization_id=organization_id,
        )

        if isinstance(dq_overview, dict):
            latest_dq_row = dq_overview.get("latest")

            if not latest_dq_row:
                dq_rows = dq_overview.get("rows") or []

                # BigQueryMetrics returns dashboard rows newest first.
                if dq_rows:
                    latest_dq_row = dq_rows[0]

            if hasattr(latest_dq_row, "model_dump"):
                latest_dq_row = latest_dq_row.model_dump()
            elif latest_dq_row is not None and not isinstance(
                latest_dq_row,
                dict,
            ):
                latest_dq_row = dict(latest_dq_row)

            if isinstance(latest_dq_row, dict):
                latest_dq_profile_run_id = str(
                    latest_dq_row.get("profile_run_id")
                    or ""
                ).strip() or None

                latest_dq_domain = str(
                    latest_dq_row.get("domain")
                    or ""
                ).strip().upper() or None

    except Exception as exc:
        # DQ evidence enriches Governance Intelligence but must not
        # make the governance overview unavailable if no profile
        # evidence can be loaded.
        logger.warning(
            "Governance overview could not load latest DQ profile "
            "evidence. organization_id=%s error_type=%s",
            organization_id,
            type(exc).__name__,
        )

    bq_client = bigquery.Client(
        project=project_id
    )

    kpi_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_INTELLIGENCE`
    WHERE organization_id = @organization_id
    """

    dataset_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_DATASET_DETAIL`
    WHERE organization_id = @organization_id
    ORDER BY
      COALESCE(certification_readiness_score, 0) ASC,
      failed_checks DESC,
      COALESCE(fair_overall_score, 0) ASC
    LIMIT 200
    """

    blockers_sql = f"""
    SELECT
      blocker_reason,
      blocker_count
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_TOP_BLOCKERS`
    WHERE organization_id = @organization_id
    LIMIT 10
    """

    policy_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_POLICY_ACTIVITY`
    WHERE organization_id = @organization_id
      AND (
        DATE(changed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
        OR changed_at IS NULL
      )
    ORDER BY changed_at DESC
    LIMIT 20
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "days",
                "INT64",
                days,
            ),
        ]
    )

    try:
        kpi_rows = [
            dict(row)
            for row in bq_client.query(
                kpi_sql,
                job_config=job_config,
            ).result()
        ]
        dataset_rows = [
            dict(row)
            for row in bq_client.query(
                dataset_sql,
                job_config=job_config,
            ).result()
        ]
        blocker_rows = [
            dict(row)
            for row in bq_client.query(
                blockers_sql,
                job_config=job_config,
            ).result()
        ]
        policy_rows = [
            dict(row)
            for row in bq_client.query(
                policy_sql,
                job_config=job_config,
            ).result()
        ]
        enriched_dataset_rows = []

        import pprint

                   
        if not kpi_rows:
            raise HTTPException(
                status_code=404,
                detail="No governance KPI data found",
            )

        kpi_row = kpi_rows[0]

        enriched_dataset_rows = []

        for dataset_row in dataset_rows:
            enriched_row = dict(dataset_row)

            enriched_row["ai_recommendation"] = (
                build_governance_ai_recommendation(
                    enriched_row,
                    organization_id=organization_id,
                    profile_run_id=(
                        latest_dq_profile_run_id
                    ),
                    domain=latest_dq_domain,
                )
            )

            enriched_dataset_rows.append(
                enriched_row
            )


        kpis = GovernanceKPI(
            total_datasets=int(
                kpi_row.get("total_datasets") or 0
            ),
            certified_datasets=int(
                kpi_row.get("certified_datasets") or 0
            ),
            ready_for_certification=int(
                kpi_row.get("ready_for_certification") or 0
            ),
            in_progress_certifications=int(
                kpi_row.get("in_progress_certifications") or 0
            ),
            avg_fair_score=float(
                kpi_row.get("avg_fair_score") or 0
            ),
            open_governance_issues=int(
                kpi_row.get("open_governance_issues") or 0
            ),
            total_checks=int(
                kpi_row.get("total_checks") or 0
            ),
            passed_checks=int(
                kpi_row.get("passed_checks") or 0
            ),
            failed_checks=int(
                kpi_row.get("failed_checks") or 0
            ),
            check_pass_rate=float(
                kpi_row.get("check_pass_rate") or 0
            ),
            active_policies=int(
                kpi_row.get("active_policies") or 0
            ),
            recent_policy_changes_30d=int(
                kpi_row.get("recent_policy_changes_30d") or 0
            ),
        )

        dataset_statuses = [GovernanceDatasetStatus(**row) for row in enriched_dataset_rows]
        top_blockers = [GovernanceBlocker(**row) for row in blocker_rows]
        policy_activity = [GovernancePolicyActivity(**row) for row in policy_rows]

        return GovernanceOverviewResponse(
            organization_id=organization_id,
            kpis=kpis,
            dataset_statuses=dataset_statuses,
            top_blockers=top_blockers,
            policy_activity=policy_activity,
            ai_feedback_metrics=ai_feedback_metrics,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load governance overview: {str(e)}",
        )


@router.get(
    "/metrics/dq-overview",
    response_model=DqDashboardResponse,
)
def get_dq_metrics_overview(
    days: int = Query(30, ge=1, le=365),
    domain: Optional[str] = Query(None),
    current_user: AuthUser = Depends(get_current_user),
):
    organization_id = require_current_organization_id(
        current_user
    )

    try:
        result = metrics.get_dq_dashboard_overview(
            days=days,
            domain=domain,
            organization_id=organization_id,
        )

        return {
            "organization_id": organization_id,
            **result,
        }

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to load DQ dashboard overview "
            "for organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.get(
    "/metrics/dq-rule-suggestions",
    response_model=DqRuleSuggestionsResponse,
)
def get_dq_rule_suggestions(
    days: int = Query(30, ge=1, le=365),
    domain: Optional[str] = Query(None),
    provider: Optional[str] = Query(
        None,
        description="Optional LLM provider override",
    ),
    use_llm: bool = Query(
        True,
        description=(
            "When false, returns deterministic rule suggestions "
            "without calling the LLM"
        ),
    ),
    current_user: AuthUser = Depends(get_current_tenant_user),
    
):
    """
    Generate implementation-ready AI data quality rule suggestions from
    the most recent Data Quality Intelligence metrics.
    """
    try:
        dq_result = metrics.get_dq_dashboard_overview(
            days=days,
            domain=domain,
            organization_id=require_current_organization_id(current_user),
        )

        if not isinstance(dq_result, dict):
            raise HTTPException(
                status_code=500,
                detail="DQ overview returned an unexpected response format.",
            )

        latest_dq_row = dq_result.get("latest")

        # Defensive fallback if the metrics service returns rows but does
        # not explicitly populate latest.
        if not latest_dq_row:
            rows = dq_result.get("rows") or []

            if rows:
                latest_dq_row = rows[0]

        if not latest_dq_row:
            return DqRuleSuggestionsResponse(
                organization_id=require_current_organization_id(
                    current_user
                ),
                days=days,
                domain=domain,
                dataset_id=None,
                dataset_name=None,
                metric_date=None,
                model_provider=(
                    provider
                    or os.getenv("QUALITY_INTELLIGENCE_LLM_PROVIDER")
                    or os.getenv("GOVERNANCE_LLM_PROVIDER")
                    or "claude"
                ),
                used_llm=False,
                ai_rule_suggestions={
                    "headline": "No DQ recommendations available yet",
                    "summary": (
                        "No Data Quality Intelligence metrics are available "
                        "for this organization in the selected lookback window."
                    ),
                    "priority": "LOW",
                    "rule_strategy": "MIXED",
                    "suggested_rules": [],
                    "recommended_sequence": [],
                    "supporting_evidence": [],
                    "confidence": 0.0,
                },
                generated_at=datetime.now(timezone.utc),
            )

        if hasattr(latest_dq_row, "model_dump"):
            latest_dq_row = latest_dq_row.model_dump()
        elif not isinstance(latest_dq_row, dict):
            latest_dq_row = dict(latest_dq_row)

        suggestions = build_quality_rule_suggestions(
            latest_dq_row,
            organization_id=str(current_user.organization_id),
            provider=provider,
            use_llm=use_llm,
)

        generated_at = datetime.now(timezone.utc)

        return DqRuleSuggestionsResponse(
            days=days,
            domain=domain or latest_dq_row.get("domain"),
            dataset_id=latest_dq_row.get("dataset_id"),
            dataset_name=(
                latest_dq_row.get("dataset_name")
                or latest_dq_row.get("table_name")
                or latest_dq_row.get("asset_name")
            ),
            metric_date=(
                str(latest_dq_row.get("metric_date"))
                if latest_dq_row.get("metric_date") is not None
                else None
            ),
            model_provider=(
                provider
                or os.getenv("QUALITY_INTELLIGENCE_LLM_PROVIDER")
                or os.getenv("GOVERNANCE_LLM_PROVIDER")
                or "claude"
            ),
            used_llm=use_llm,
            ai_rule_suggestions=suggestions,
            generated_at=generated_at,
            organization_id=require_current_organization_id(
                                current_user
            ),
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to generate DQ rule suggestions."
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to generate DQ rule suggestions: "
                f"{str(exc)}"
            ),
        ) from exc


@router.get(
    "/metrics/overview",
    response_model=MetricsOverviewResponse,
)
def metrics_overview(
    days: int = Query(7, ge=1, le=365),
    current_user: AuthUser = Depends(get_current_tenant_user),
):
    try:
        data = metrics.overview(
            days,
            organization_id=require_current_organization_id(current_user),
        )

        payload = {
            "days": days,
            "generated_at": utc_now_iso(),
            **data,
        }

        return JSONResponse(content=jsonable_encoder(payload))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
