from __future__ import annotations

import uuid

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)


ORG = "org_00003"


def main() -> None:
    repo = QualityProfilerRepository()

    suffix = uuid.uuid4().hex[:8]
    run_id = f"dqp_persisttest_{suffix}"

    print("\n1. Creating durable profile...")

    profile = repo.create_profile(
        organization_id=ORG,
        profile_name=f"Persistence Test {suffix}",
        domain="PRODUCT",
        source_name="PERSISTENCE_TEST_SOURCE",
        business_key_field="product_id",
        created_by="repository_persistence_test",
    )

    profile_id = profile["profile_id"]

    print(f"   profile_id={profile_id}")

    assert profile_id.startswith("prof_")
    assert profile["organization_id"] == ORG

    print("   PASS: durable profile created.")

    # ---------------------------------------------------------
    # Retrieve durable profile
    # ---------------------------------------------------------

    print("\n2. Retrieving durable profile...")

    loaded_profile = repo.get_profile(
        organization_id=ORG,
        profile_id=profile_id,
    )

    assert loaded_profile is not None
    assert loaded_profile["profile_id"] == profile_id
    assert loaded_profile["organization_id"] == ORG
    assert loaded_profile["domain"] == "PRODUCT"

    print("   PASS: durable profile retrieved.")

    # ---------------------------------------------------------
    # Bind one execution run to the durable profile
    # Use a fake-but-valid BigQuery source context.
    # No source connection is actually opened here.
    # ---------------------------------------------------------

    print("\n3. Binding dqp run to durable profile...")

    repo.save_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id,
        profile_id=profile_id,
        source_type="BIGQUERY",
        connection_id="conn_persistence_test",
        project_id="api-project-503305938314",
        dataset_id="ai_data_steward_mvp",
        table_id="DQ_PROFILE_CONFIG",
    )

    print(f"   profile_run_id={run_id}")
    print("   PASS: profile run binding persisted.")

    # ---------------------------------------------------------
    # Retrieve exact prof_ -> dqp_ binding
    # ---------------------------------------------------------

    print("\n4. Retrieving prof_ -> dqp_ binding...")

    context = repo.get_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id,
        profile_id=profile_id,
    )

    assert context is not None
    assert context["organization_id"] == ORG
    assert context["profile_id"] == profile_id
    assert context["profile_run_id"] == run_id
    assert context["source_type"] == "BIGQUERY"

    print("   PASS: exact durable profile/run binding retrieved.")
    print("\n4b. Testing legacy run-only source-context lookup...")

    legacy_context = repo.get_profile_source_context(
    organization_id=ORG,
    profile_run_id=run_id,
    )

    assert legacy_context is not None
    assert legacy_context["organization_id"] == ORG
    assert legacy_context["profile_run_id"] == run_id
    assert legacy_context["profile_id"] == profile_id

    print("   PASS: legacy remediation-compatible lookup preserved.")

    # ---------------------------------------------------------
    # Wrong profile must fail closed
    # ---------------------------------------------------------

    print("\n5. Testing wrong-profile isolation...")

    wrong_profile = f"prof_{uuid.uuid4().hex[:20]}"

    wrong_profile_result = repo.get_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id,
        profile_id=wrong_profile,
    )

    assert wrong_profile_result is None

    print("   PASS: wrong profile cannot retrieve run binding.")

    # ---------------------------------------------------------
    # Wrong tenant must fail closed
    # ---------------------------------------------------------

    print("\n6. Testing cross-tenant isolation...")

    wrong_org_result = repo.get_profile_source_context(
        organization_id="org_persistence_isolation_test",
        profile_run_id=run_id,
        profile_id=profile_id,
    )

    assert wrong_org_result is None

    wrong_org_profile = repo.get_profile(
        organization_id="org_persistence_isolation_test",
        profile_id=profile_id,
    )

    assert wrong_org_profile is None

    print("   PASS: cross-tenant access denied.")
	
        # ---------------------------------------------------------
    # Same durable profile across multiple independent runs
    # ---------------------------------------------------------

    print("\n7. Testing durable profile across multiple dqp runs...")

    run_id_2 = f"dqp_persisttest_{uuid.uuid4().hex[:8]}"

    repo.save_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id_2,
        profile_id=profile_id,
        source_type="BIGQUERY",
        connection_id="conn_persistence_test",
        project_id="api-project-503305938314",
        dataset_id="ai_data_steward_mvp",
        table_id="DQ_PROFILE_CONFIG",
    )

    context_1 = repo.get_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id,
        profile_id=profile_id,
    )

    context_2 = repo.get_profile_source_context(
        organization_id=ORG,
        profile_run_id=run_id_2,
        profile_id=profile_id,
    )

    assert context_1 is not None
    assert context_2 is not None

    assert context_1["profile_id"] == profile_id
    assert context_2["profile_id"] == profile_id

    assert context_1["profile_run_id"] == run_id
    assert context_2["profile_run_id"] == run_id_2
    assert context_1["profile_run_id"] != context_2["profile_run_id"]

    # Durable definition must still be the same record.
    durable_profile = repo.get_profile(
        organization_id=ORG,
        profile_id=profile_id,
    )

    assert durable_profile is not None
    assert durable_profile["profile_id"] == profile_id

    print(f"   durable profile : {profile_id}")
    print(f"   run 1           : {run_id}")
    print(f"   run 2           : {run_id_2}")
    print("   PASS: same prof_ survives across independent dqp_ runs.")

    # ---------------------------------------------------------
    # Final relationship
    # ---------------------------------------------------------

    print("\n========================================")
    print("PROFILE PERSISTENCE TEST PASSED")
    print("========================================")
    print(f"organization_id : {ORG}")
    print(f"profile_id      : {profile_id}")
    print(f"profile_run_1   : {run_id}")
    print(f"profile_run_2   : {run_id_2}")
    print("relationship    : org_ -> prof_ -> multiple dqp_ runs")
    print("========================================\n")


if __name__ == "__main__":
    main()


