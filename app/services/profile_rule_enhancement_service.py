from __future__ import annotations

from email.mime import text
import json
import re
from dataclasses import dataclass
from typing import Any

from app.api.schemas import ProfileRuleGovernanceIdentityResponse
from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)

from app.services.profile_rule_registry import (
    ProfileRuleDefinition,
    RuleEvaluator,
    RuleFamily,
    RuleScope,
    RuleStatus,
)

SUPPORTED_OPERATORS = {
    "CONTAINS",
    "EQUALS",
    "REGEX",
    "IN",
}

SUPPORTED_REMEDIATION_ACTIONS = {
    "STANDARDIZE_VALUE",
    "REPLACE_VALUE",
    "DELETE_VALUE",
    "FLAG_FOR_REVIEW",
}




@dataclass(frozen=True)
class ProfileRuleInterpretation:
    field_name: str
    rule_name: str
    rule_type: str
    operator: str
    comparison_value: Any | None
    remediation_action: str
    replacement_value: Any | None
    explanation: str

@dataclass(frozen=True)
class ProfileRuleOpportunityEvidence:
    current_value: Any
    proposed_value: Any | None
    affected_record_count: int

@dataclass(frozen=True)
class ProfileRuleOpportunity:
    field_name: str
    pattern_summary: str

    affected_record_count: int
    sample_values: tuple[Any, ...]
    evidence_examples: tuple[
        ProfileRuleOpportunityEvidence,
        ...
    ]

    suggested_rule_type: str
    suggested_operator: str
    suggested_comparison_value: Any | None

    suggested_action: str
    suggested_replacement_value: Any | None

    confidence: float
    explanation: str

    requires_steward_review: bool = True


class ProfileRuleEnhancementService:
    """
    Governed orchestration service for profile-scoped rule enhancement.

    Responsibilities:
    - validate tenant/profile/run identity
    - interpret steward intent into a constrained candidate rule
    - persist CANDIDATE state
    - deterministically evaluate candidate impact
    - persist PREVIEWED state
    - prepare approved PROFILE rule activation

    This service must never directly mutate source-system data.
    """

    def __init__(
        self,
        *,
        quality_repository: QualityProfilerRepository,
        llm_service: Any | None = None,
    ) -> None:
        self.quality_repository = quality_repository
        self.llm_service = llm_service

    # ============================================================
    # Candidate analysis
    # ============================================================

    def analyze_instruction(
        self,
        *,
        organization_id: str,
        profile_id: str,
        profile_run_id: str,
        instruction: str,
        created_by: str,
    ) -> dict[str, Any]:
        normalized_instruction = str(
            instruction or ""
        ).strip()

        if len(normalized_instruction) < 3:
            raise ValueError(
                "Profile rule instruction is required."
            )

        profile = self.quality_repository.get_profile(
            organization_id=organization_id,
            profile_id=profile_id,
        )

        if not profile:
            raise ValueError(
                "Governed profile was not found."
            )

        if (
            str(profile.get("status") or "")
            .strip()
            .upper()
            != "ACTIVE"
        ):
            raise ValueError(
                "Profile rule enhancement requires "
                "an ACTIVE governed profile."
            )

        source_context = (
            self.quality_repository
            .get_profile_source_context(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                profile_id=profile_id,
            )
        )

        if not source_context:
            raise ValueError(
                "Profile run does not belong to the "
                "governed profile."
            )

        domain = str(
            profile.get("domain") or ""
        ).strip().upper()

        interpretation = (
            self._interpret_instruction(
                instruction=normalized_instruction,
                domain=domain,
            )
        )

        self._validate_interpretation(
            interpretation
        )

        return (
            self.quality_repository.create_profile_rule_candidate(
                organization_id=organization_id,
                profile_id=profile_id,
                profile_run_id=profile_run_id,
                domain=domain,
                field_name=interpretation.field_name,
                rule_name=interpretation.rule_name,
                rule_type=interpretation.rule_type,
                operator=interpretation.operator,
                comparison_value_json=self._to_json(
                    interpretation.comparison_value
                ),
                remediation_action=interpretation.remediation_action,
                replacement_value_json=(
                    self._to_json(
                        interpretation.replacement_value
                    )
                    if interpretation.replacement_value is not None
                    else None
                ),

                # NEW
                candidate_source="STEWARD_INSTRUCTION",

                steward_instruction=normalized_instruction,
                ai_explanation=interpretation.explanation,
                created_by=created_by,
            )
                    )
    

    def discover_rule_opportunities(
            self,
            *,
            organization_id: str,
            profile_id: str,
            profile_run_id: str,
            values_by_field: dict[
                str,
                list[dict[str, Any]],
            ],
        ) -> list[ProfileRuleOpportunity]:
            """
            Discover repeated suspicious exact-value patterns that may justify
            a profile-scoped governed rule.

            Discovery is advisory only. It must never:
            - activate a rule
            - modify the Profile Rule Registry
            - modify source data
            - automatically approve remediation

            This first discovery pass is intentionally conservative.
            """

            profile = self.quality_repository.get_profile(
                organization_id=organization_id,
                profile_id=profile_id,
            )

            if not profile:
                raise ValueError(
                    "Governed profile was not found."
                )

            if (
                str(profile.get("status") or "")
                .strip()
                .upper()
                != "ACTIVE"
            ):
                raise ValueError(
                    "Profile rule discovery requires "
                    "an ACTIVE governed profile."
                )

            source_context = (
                self.quality_repository
                .get_profile_source_context(
                    organization_id=organization_id,
                    profile_run_id=profile_run_id,
                    profile_id=profile_id,
                )
            )

            if not source_context:
                raise ValueError(
                    "Profile run does not belong to the "
                    "governed profile."
                )

            opportunities: list[
                ProfileRuleOpportunity
            ] = []

            for field_name, values in values_by_field.items():
                normalized_field_name = str(
                    field_name or ""
                ).strip()

                if not normalized_field_name:
                    continue

                value_counts: dict[str, int] = {}

                for item in values:
                    raw_value = item.get("value")

                    if raw_value is None:
                        continue

                    text_value = str(raw_value)

                    if not text_value.strip():
                        continue

                    value_counts[text_value] = (
                        value_counts.get(text_value, 0)
                        + 1
                    )

                whitespace_evidence: list[
                    ProfileRuleOpportunityEvidence
                ] = []

                for (
                    observed_value,
                    affected_count,
                ) in value_counts.items():
                    # Continue to ignore one-off noise.
                    if affected_count < 2:
                        continue

                    # Blank or whitespace-only values are completeness
                    # concerns, not formatting-normalization candidates.
                    if not observed_value.strip():
                        continue

                    has_repeated_whitespace = bool(
                        re.search(
                            r"\s{2,}",
                            observed_value,
                        )
                    )

                    has_edge_whitespace = (
                        observed_value
                        != observed_value.strip()
                    )

                    if not (
                        has_repeated_whitespace
                        or has_edge_whitespace
                    ):
                        continue

                    proposed_value = re.sub(
                        r"\s+",
                        " ",
                        observed_value.strip(),
                    )

                    # Defensive: discovery should only surface
                    # evidence where normalization changes value.
                    if proposed_value == observed_value:
                        continue

                    whitespace_evidence.append(
                        ProfileRuleOpportunityEvidence(
                            current_value=observed_value,
                            proposed_value=proposed_value,
                            affected_record_count=(
                                affected_count
                            ),
                        )
                    )

                if not whitespace_evidence:
                    continue

                whitespace_evidence.sort(
                    key=lambda item: (
                        -item.affected_record_count,
                        str(item.current_value),
                    )
                )

                total_affected = sum(
                    item.affected_record_count
                    for item in whitespace_evidence
                )

                evidence_examples = tuple(
                    whitespace_evidence[:5]
                )

                opportunities.append(
                    ProfileRuleOpportunity(
                        field_name=normalized_field_name,
                        pattern_summary=(
                            "Whitespace formatting "
                            "inconsistency"
                        ),
                        affected_record_count=(
                            total_affected
                        ),
                        sample_values=tuple(
                            item.current_value
                            for item in evidence_examples
                        ),
                        evidence_examples=(
                            evidence_examples
                        ),
                        suggested_rule_type=(
                            "INVALID_PATTERN"
                        ),
                        suggested_operator="REGEX",
                        suggested_comparison_value=(r"(?=.*\S)(?:\s{2,}|^\s+|\s+$)"),
                        suggested_action=(
                            "FLAG_FOR_REVIEW"
                        ),
                        suggested_replacement_value=None,
                        confidence=0.80,
                        explanation=(
                            f"I found {total_affected} "
                            "records in "
                            f"{normalized_field_name} "
                            "with repeated or edge "
                            "whitespace formatting. "
                            "Compatible observations were "
                            "grouped into one discovery "
                            "opportunity. Review the "
                            "current and proposed values "
                            "before creating a "
                            "profile-specific rule."
                        ),
                        requires_steward_review=True,
                    )
                )

            return sorted(
                opportunities,
                key=lambda item: (
                    -item.affected_record_count,
                    item.field_name,
                    item.pattern_summary,
                    item.suggested_action,
                ),
            )

    def create_discovery_candidate(
        self,
        *,
        organization_id: str,
        profile_id: str,
        profile_run_id: str,
        field_name: str,
        pattern_summary: str,
        rule_type: str,
        operator: str,
        comparison_value: Any,
        remediation_action: str,
        replacement_value: Any | None,
        confidence: float,
        explanation: str,
        created_by: str,
    ) -> dict[str, Any]:
        """
        Persist a steward-selected discovery opportunity as a
        governed profile-rule candidate.

        This does not activate a rule or mutate source data.
        """

        profile = self.quality_repository.get_profile(
            organization_id=organization_id,
            profile_id=profile_id,
        )

        if not profile:
            raise ValueError(
                "Governed profile was not found."
            )

        if (
            str(profile.get("status") or "")
            .strip()
            .upper()
            != "ACTIVE"
        ):
            raise ValueError(
                "Discovery candidate requires an ACTIVE governed profile."
            )

        source_context = (
            self.quality_repository.get_profile_source_context(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                profile_id=profile_id,
            )
        )

        if not source_context:
            raise ValueError(
                "Profile run does not belong to the governed profile."
            )

        normalized_field = str(field_name or "").strip()
        normalized_summary = str(pattern_summary or "").strip()
        normalized_rule_type = str(rule_type or "").strip().upper()
        normalized_operator = str(operator or "").strip().upper()
        normalized_action = str(
            remediation_action or ""
        ).strip().upper()
        normalized_explanation = str(explanation or "").strip()

        if not normalized_field:
            raise ValueError("field_name is required.")

        if not normalized_summary:
            raise ValueError("pattern_summary is required.")

        if comparison_value is None:
            raise ValueError(
                "Discovery candidate requires a comparison value."
            )

        domain = str(profile.get("domain") or "").strip().upper()

        if not domain:
            raise ValueError(
                "Governed profile domain is required."
            )

        return self.quality_repository.create_profile_rule_candidate(
            organization_id=organization_id,
            profile_id=profile_id,
            profile_run_id=profile_run_id,
            domain=domain,
            field_name=normalized_field,
            rule_name=(
                f"Discovered {normalized_summary} - "
                f"{normalized_field}"
            ),
            rule_type=normalized_rule_type,
            operator=normalized_operator,
            comparison_value_json=self._to_json(
                comparison_value
            ),
            remediation_action=normalized_action,
            replacement_value_json=(
                self._to_json(replacement_value)
                if replacement_value is not None
                else None
            ),
            steward_instruction=(
                "Review AI-discovered profile pattern: "
                f"{normalized_summary}"
            ),
            candidate_source="AI_PATTERN_DISCOVERY",
            discovery_summary=normalized_summary,
            discovery_confidence=float(confidence),
            ai_explanation=normalized_explanation,
            created_by=created_by,
        )

    # ============================================================
    # Deterministic preview
    # ============================================================

    def preview_candidate(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        values: list[dict[str, Any]],
        preview_limit: int = 25,
    ) -> dict[str, Any]:
        """
        Evaluate a persisted candidate against supplied profile values.

        `values` must contain records shaped like:

        {
            "source_row_id": "...",
            "record_id": "...",
            "value": <raw value>
        }

        No source mutation occurs here.
        """

        candidate = (
            self.quality_repository
            .get_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
            )
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        status = str(
            candidate.get("status") or ""
        ).strip().upper()

        if status not in {
            "CANDIDATE",
            "PREVIEWED",
        }:
            raise ValueError(
                "Only a candidate rule may be previewed."
            )

        operator = str(
            candidate.get("operator") or ""
        ).strip().upper()

        remediation_action = str(
            candidate.get(
                "remediation_action"
            )
            or ""
        ).strip().upper()

        comparison_value = self._from_json(
            candidate.get(
                "comparison_value_json"
            )
        )

        replacement_value = self._from_json(
            candidate.get(
                "replacement_value_json"
            )
        )

        affected_count = 0
        preview_rows: list[
            dict[str, Any]
        ] = []

        safe_preview_limit = max(
            1,
            min(
                int(preview_limit),
                100,
            ),
        )

        for item in values:
            current_value = item.get("value")

            if not self._matches(
                current_value=current_value,
                operator=operator,
                comparison_value=comparison_value,
            ):
                continue

            affected_count += 1

            suggested_value = (
                self._propose_value(
                    current_value=current_value,
                    remediation_action=(
                        remediation_action
                    ),
                    replacement_value=(
                        replacement_value
                    ),
                )
            )

            if (
                len(preview_rows)
                < safe_preview_limit
            ):
                preview_rows.append(
                    {
                        "source_row_id":
                            item.get(
                                "source_row_id"
                            ),
                        "record_id":
                            item.get(
                                "record_id"
                            ),
                        "field_name":
                            candidate.get(
                                "field_name"
                            ),
                        "current_value":
                            current_value,
                        "suggested_value":
                            suggested_value,
                        "suggested_action":
                            remediation_action,
                    }
                )

        records_evaluated = len(values)

        self.quality_repository.mark_profile_rule_candidate_previewed(
            organization_id=organization_id,
            candidate_id=candidate_id,
            records_evaluated=records_evaluated,
            affected_record_count=affected_count,
        )

        return {
            "organization_id":
                organization_id,
            "candidate_id":
                candidate_id,
            "profile_id":
                candidate.get("profile_id"),
            "profile_run_id":
                candidate.get(
                    "profile_run_id"
                ),
            "field_name":
                candidate.get("field_name"),
            "records_evaluated":
                records_evaluated,
            "affected_record_count":
                affected_count,
            "preview":
                preview_rows,
            "source_mutated":
                False,
        }

    def approve_candidate(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        organization_id = str(
            organization_id or ""
        ).strip()

        candidate_id = str(
            candidate_id or ""
        ).strip()

        approved_by = str(
            approved_by or ""
        ).strip()

        if not organization_id.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ "
                "tenant identifier standard."
            )

        if not candidate_id.startswith("prc_"):
            raise ValueError(
                "candidate_id must use the prc_ "
                "identifier standard."
            )

        if not approved_by:
            raise ValueError("approved_by is required.")

        candidate = (
            self.quality_repository.get_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
            )
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        candidate_org = str(
            candidate.get("organization_id") or ""
        ).strip()

        if candidate_org != organization_id:
            raise ValueError(
                "Profile rule candidate crossed tenant boundary."
            )

        candidate_status = str(
            candidate.get("status") or ""
        ).strip().upper()

        #
        # Idempotent approval retry.
        #
        # If the candidate is already APPROVED, the persisted governed
        # rule is authoritative. Resolve it and verify that the candidate
        # linkage still matches before returning the existing activation.
        #
        if candidate_status == "APPROVED":
            profile_id = str(
                candidate.get("profile_id") or ""
            ).strip()

            existing_rule = (
                self.quality_repository.get_profile_rule_by_candidate(
                    organization_id=organization_id,
                    profile_id=profile_id,
                    source_candidate_id=candidate_id,
                )
            )

            if not existing_rule:
                raise RuntimeError(
                    "Profile rule candidate is APPROVED but its "
                    "governed ACTIVE rule could not be resolved."
                )

            activated_rule_id = str(
                existing_rule.get("rule_id") or ""
            ).strip()

            activated_rule_version = int(
                existing_rule.get("version") or 0
            )

            if not activated_rule_id:
                raise RuntimeError(
                    "Approved profile rule does not have a rule_id."
                )

            if activated_rule_version < 1:
                raise RuntimeError(
                    "Approved profile rule does not have a valid version."
                )

            linked_rule_id = str(
                candidate.get("activated_rule_id") or ""
            ).strip()

            linked_rule_version = int(
                candidate.get("activated_rule_version") or 0
            )

            if (
                linked_rule_id != activated_rule_id
                or linked_rule_version != activated_rule_version
            ):
                raise RuntimeError(
                    "Approved candidate linkage does not match its "
                    "governed ACTIVE rule."
                )

            return {
                "candidate_id": candidate_id,
                "organization_id": organization_id,
                "profile_id": profile_id,
                "profile_run_id": str(
                    candidate.get("profile_run_id") or ""
                ).strip(),
                "rule_id": activated_rule_id,
                "logical_key": str(
                    existing_rule.get("logical_key") or ""
                ).strip(),
                "version": activated_rule_version,
                "scope": str(
                    existing_rule.get("scope") or "PROFILE"
                ).strip().upper(),
                "status": str(
                    existing_rule.get("status") or "ACTIVE"
                ).strip().upper(),
                "field_name": str(
                    existing_rule.get("field_name") or ""
                ).strip(),
                "remediation_action": str(
                    candidate.get("remediation_action") or ""
                ).strip().upper(),
                "approved_by": str(
                    candidate.get("approved_by") or approved_by
                ).strip(),
                "approved_at": candidate.get("approved_at"),
                "requires_reprofile": True,
            }

        if candidate_status != "PREVIEWED":
            raise ValueError(
                "Only a PREVIEWED profile rule candidate "
                "may be approved."
            )

        #
        # Candidate semantics -> canonical governed rule contract.
        #
        # ProfileRuleDefinition performs the registry-level
        # validation before anything is persisted as ACTIVE.
        #
        definition = self._candidate_to_profile_rule_definition(
            candidate=candidate,
            organization_id=organization_id,
            approved_by=approved_by,
        )

        if definition.scope != RuleScope.PROFILE:
            raise ValueError(
                "Profile rule approval produced a non-PROFILE rule."
            )

        if definition.status != RuleStatus.ACTIVE:
            raise ValueError(
                "Profile rule approval must produce an ACTIVE rule."
            )

        if definition.organization_id != organization_id:
            raise ValueError(
                "Canonical profile rule crossed tenant boundary."
            )

        if definition.profile_id != candidate.get("profile_id"):
            raise ValueError(
                "Canonical profile rule crossed profile boundary."
            )

        domains = tuple(definition.domains or ())
        field_names = tuple(definition.field_names or ())

        if len(domains) != 1:
            raise ValueError(
                "Approved profile rule must resolve exactly "
                "one domain."
            )

        if len(field_names) != 1:
            raise ValueError(
                "Approved profile rule must resolve exactly "
                "one field."
            )

        #
        # Persist the real governed ACTIVE rule.
        #
        # The repository generates the actual rule_id and version.
        # Existing candidate linkage is checked first to make the
        # persistence step safe for a mid-flight retry.
        #
        existing_rule = (
            self.quality_repository.get_profile_rule_by_candidate(
                organization_id=organization_id,
                profile_id=definition.profile_id,
                source_candidate_id=candidate_id,
            )
        )

        if existing_rule:
            persisted_rule = existing_rule
        else:
            persisted_rule = (
                self.quality_repository.create_active_profile_rule(
                    organization_id=organization_id,
                    profile_id=definition.profile_id,
                    source_candidate_id=candidate_id,
                    logical_key=definition.logical_key,
                    family=definition.family.value,
                    evaluator=definition.evaluator.value,
                    domain=domains[0],
                    field_name=field_names[0],
                    parameters=dict(definition.parameters),
                    severity=definition.severity,
                    description=definition.description,
                    remediation_available=(
                        definition.remediation_available
                    ),
                    remediation_type=definition.remediation_type,
                    approved_by=approved_by,
                )
            )

        activated_rule_id = str(
            persisted_rule.get("rule_id") or ""
        ).strip()

        activated_rule_version = int(
            persisted_rule.get("version") or 0
        )
        activated_logical_key = str(
        persisted_rule.get("logical_key") or ""
        ).strip()

        if not activated_logical_key:
                raise RuntimeError(
                    "Persisted governed profile rule is missing "
                    "logical_key."
                )

        if not activated_rule_id:
            raise RuntimeError(
                "Governed profile rule persistence did not "
                "return a rule_id."
            )

        if activated_rule_version < 1:
            raise RuntimeError(
                "Governed profile rule persistence did not "
                "return a valid version."
            )

        #
        # Only after governed persistence succeeds do we mark the
        # candidate APPROVED and link it to the real activated rule.
        #
        approved_candidate = (
            self.quality_repository.approve_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
                approved_by=approved_by,
                activated_rule_id=activated_rule_id,
                activated_rule_version=activated_rule_version,
            )
        )

        if not approved_candidate:
            raise RuntimeError(
                "Governed rule was activated but candidate "
                "approval could not be reloaded."
            )

        approved_status = str(
            approved_candidate.get("status") or ""
        ).strip().upper()

        if approved_status != "APPROVED":
            raise RuntimeError(
                "Profile rule candidate did not transition "
                "to APPROVED."
            )

        linked_rule_id = str(
            approved_candidate.get("activated_rule_id") or ""
        ).strip()

        linked_rule_version = int(
            approved_candidate.get(
                "activated_rule_version"
            )
            or 0
        )

        if (
            linked_rule_id != activated_rule_id
            or linked_rule_version != activated_rule_version
        ):
            raise RuntimeError(
                "Approved candidate does not reference the "
                "persisted governed rule."
            )

        return {
            "candidate_id": candidate_id,
            "organization_id": organization_id,
            "profile_id": definition.profile_id,
            "profile_run_id": str(
                candidate.get("profile_run_id") or ""
            ).strip(),
            "rule_id": activated_rule_id,
            "logical_key": activated_logical_key,
            "version": activated_rule_version,
            "scope": "PROFILE",
            "status": "ACTIVE",
            "field_name": field_names[0],
            "remediation_action": str(
                candidate.get("remediation_action") or ""
            ).strip().upper(),
            "approved_by": approved_by,
            "approved_at": approved_candidate.get(
                "approved_at"
            ),
            "requires_reprofile": True,
        }

    def _resolve_source_column(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        field_name: str,
    ) -> str:
        normalized_field = str(
            field_name or ""
        ).strip()

        if not normalized_field:
            raise ValueError(
                "field_name is required."
            )

        summary = (
            self.quality_repository.get_profile_summary(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
            )
        )

        if not summary:
            raise ValueError(
                "Profile summary was not found for the "
                "governed profile execution."
            )

        mappings = summary.get("column_mappings") or []

        if isinstance(mappings, str):
            try:
                mappings = json.loads(mappings)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Profile column mappings are invalid."
                ) from exc

        if not isinstance(mappings, list):
            raise ValueError(
                "Profile column mappings are invalid."
            )

        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue

            target_field = str(
                mapping.get("target_field") or ""
            ).strip()

            if target_field != normalized_field:
                continue

            source_column = str(
                mapping.get("source_column") or ""
            ).strip()

            if not source_column:
                raise ValueError(
                    "The governed profile mapping contains "
                    f"no source column for '{normalized_field}'."
                )

            return source_column

        raise ValueError(
            "The governed profile execution contains no "
            f"source-column mapping for '{normalized_field}'."
        )

    def resolve_candidate_governance_identity(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        resolved_by: str,
    ) -> ProfileRuleGovernanceIdentityResponse:
        """
        Resolve a persisted profile-rule candidate to its canonical
        governed identity without activating or mutating governance.

        This reuses the same governed compiler used during approval.
        """
        candidate = (
            self.quality_repository
            .get_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
            )
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        definition = self._candidate_to_profile_rule_definition(
            candidate=candidate,
            organization_id=organization_id,
            approved_by=resolved_by,
        )

        logical_key = str(
            definition.logical_key or ""
        ).strip()

        if not logical_key:
            raise RuntimeError(
                "Governed profile rule compiler did not produce "
                "a logical_key."
            )

        return ProfileRuleGovernanceIdentityResponse(
            candidate_id=candidate_id,
            profile_id=definition.profile_id,
            field_name=definition.field_names[0],
            logical_key=logical_key,
        )

    # ============================================================
    # Deterministic rule evaluation
    # ============================================================

    def _candidate_to_profile_rule_definition(
        self,
        *,
        candidate: dict[str, Any],
        organization_id: str,
        approved_by: str,
    ) -> ProfileRuleDefinition:
        organization_id = str(
            organization_id or ""
        ).strip()

        approved_by = str(
            approved_by or ""
        ).strip()

        if not organization_id.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ "
                "tenant identifier standard."
            )

        if not approved_by:
            raise ValueError("approved_by is required.")

        candidate_org = str(
            candidate.get("organization_id") or ""
        ).strip()

        if candidate_org != organization_id:
            raise ValueError(
                "Profile rule candidate crossed tenant boundary."
            )

        candidate_id = str(
            candidate.get("candidate_id") or ""
        ).strip()

        if not candidate_id.startswith("prc_"):
            raise ValueError(
                "candidate_id must use the prc_ "
                "identifier standard."
            )

        profile_id = str(
            candidate.get("profile_id") or ""
        ).strip()

        if not profile_id.startswith("prof_"):
            raise ValueError(
                "profile_id must use the prof_ "
                "identifier standard."
            )

        domain = str(
            candidate.get("domain") or ""
        ).strip().upper()

        field_name = str(
            candidate.get("field_name") or ""
        ).strip().lower()

        rule_type = str(
            candidate.get("rule_type") or ""
        ).strip().upper()

        operator = str(
            candidate.get("operator") or ""
        ).strip().upper()

        remediation_action = str(
            candidate.get("remediation_action") or ""
        ).strip().upper()

        comparison_value = self._from_json(
            candidate.get("comparison_value_json")
        )

        replacement_value = self._from_json(
            candidate.get("replacement_value_json")
        )

        if not domain:
            raise ValueError(
                "Candidate domain is required."
            )

        if not field_name:
            raise ValueError(
                "Candidate field_name is required."
            )

        if rule_type != "INVALID_PATTERN":
            raise ValueError(
                "Unsupported candidate rule_type for governed "
                f"profile activation: {rule_type!r}."
            )

        pattern_summary = str(
            candidate.get("discovery_summary") or ""
        ).strip()

        #
        # Compile candidate semantics into the canonical governed
        # execution contract.
        #
        # Candidate operators describe how candidate evidence was
        # discovered/previewed. They are not necessarily the same
        # evaluator used by the ACTIVE governed rule.
        #
        evaluator: RuleEvaluator
        parameters: dict[str, Any]
        remediation_available: bool
        remediation_type: str | None
        family: RuleFamily
        severity: str

        if (
            operator == "REGEX"
            and remediation_action == "FLAG_FOR_REVIEW"
            and pattern_summary.lower()
            == "whitespace formatting inconsistency"
        ):
            #
            # Whitespace discovery compiles to the canonical
            # deterministic WHITESPACE evaluator.
            #
            # Do not persist the discovery regex as the ACTIVE
            # contract. REGEX in UniversalProfileRulesEngine is a
            # valid-state evaluator, while the discovery regex
            # identifies anomalous values.
            #
            evaluator = RuleEvaluator.WHITESPACE
            parameters = {}
            remediation_available = False
            remediation_type = None
            family = RuleFamily.STANDARDIZATION
            severity = "LOW"

        elif (
            operator in {"EQUALS", "CONTAINS"}
            and remediation_action == "DELETE_VALUE"
        ):
            if replacement_value is not None:
                raise ValueError(
                    "DELETE_VALUE candidate must not define "
                    "a replacement value."
                )

            if not isinstance(comparison_value, str):
                raise ValueError(
                    f"{operator} candidate requires a string "
                    "comparison value."
                )

            #
            # Preserve the persisted comparison value exactly.
            #
            # Exact whitespace can be part of the governed invalid
            # value, particularly for EQUALS.
            #
            if comparison_value == "":
                raise ValueError(
                    f"{operator} candidate comparison value "
                    "cannot be empty."
                )

            escaped_value = re.escape(comparison_value)

            #
            # Universal REGEX rules are valid-state contracts.
            # Compile the known invalid value/token into its inverse.
            #
            if operator == "EQUALS":
                pattern = rf"^(?!{escaped_value}$).*$"
            else:
                pattern = rf"^(?!.*{escaped_value}).*$"

            evaluator = RuleEvaluator.REGEX
            parameters = {
                "pattern": pattern,
            }
            remediation_available = True
            remediation_type = "DELETE_VALUE"
            family = RuleFamily.VALIDITY
            severity = "HIGH"

        else:
            #
            # Fail closed. A newly introduced discovery/operator/action
            # combination must receive an explicit governed compiler
            # mapping before it can become ACTIVE.
            #
            raise ValueError(
                "Unsupported candidate semantics for governed "
                "profile activation: "
                f"rule_type={rule_type!r}, "
                f"operator={operator!r}, "
                f"remediation_action={remediation_action!r}, "
                f"pattern_summary={pattern_summary!r}."
            )

        logical_key = (
            f"PROFILE:{profile_id}:"
            f"{field_name.upper()}:{rule_type}:"
            f"{evaluator.value}"
        )

        description = str(
            candidate.get("ai_explanation")
            or candidate.get("rule_name")
            or (
                f"Profile-specific {rule_type.lower()} rule "
                f"for {field_name}."
            )
        ).strip()

        now = self._utc_now()

        return ProfileRuleDefinition(
            # Temporary canonical identity for validation only.
            # Persistence generates the real governed rule_id.
            rule_id=(
                f"CANDIDATE_{candidate_id.upper()}"
            ),
            version="1",
            logical_key=logical_key,
            scope=RuleScope.PROFILE,
            family=family,
            evaluator=evaluator,
            status=RuleStatus.ACTIVE,
            severity=severity,
            description=description,
            organization_id=organization_id,
            profile_id=profile_id,
            domains=(domain,),
            field_names=(field_name,),
            parameters=parameters,
            deterministic=True,
            remediation_available=remediation_available,
            remediation_type=remediation_type,
            origin="STEWARD_APPROVED",
            created_by=approved_by,
            approved_by=approved_by,
            activated_by=approved_by,
            approved_at=now,
            activated_at=now,
            effective_from=now,
        )
    @staticmethod
    def _matches(
        *,
        current_value: Any,
        operator: str,
        comparison_value: Any,
    ) -> bool:
        if current_value is None:
            return False

        text = str(current_value)

        if operator == "CONTAINS":
            if comparison_value is None:
                return False

            return (
                str(comparison_value)
                in text
            )

        if operator == "EQUALS":
            return (
                text
                == str(comparison_value)
            )

        if operator == "REGEX":
            if comparison_value is None:
                return False

            try:
                return bool(
                    re.search(
                        str(comparison_value),
                        text,
                    )
                )
            except re.error as exc:
                raise ValueError(
                    "Profile rule contains an "
                    "invalid REGEX pattern."
                ) from exc

        if operator == "IN":
            if not isinstance(
                comparison_value,
                list,
            ):
                raise ValueError(
                    "IN profile rules require "
                    "a list comparison value."
                )

            normalized_values = {
                str(value)
                for value in comparison_value
            }

            return text in normalized_values

        raise ValueError(
            "Unsupported profile rule operator."
        )

    @staticmethod
    def _propose_value(
        *,
        current_value: Any,
        remediation_action: str,
        replacement_value: Any,
    ) -> Any:
        if remediation_action == "DELETE_VALUE":
            # Governed semantics:
            # clear the field value only.
            # Never delete the source record.
            return None

        if remediation_action == "REPLACE_VALUE":
            if replacement_value is None:
                raise ValueError(
                    "REPLACE_VALUE requires a "
                    "replacement value."
                )

            return replacement_value

        if remediation_action == "STANDARDIZE_VALUE":
            if replacement_value is not None:
                return replacement_value

            # A future governed standardization
            # contract may calculate this value.
            # Until then, fail closed rather than
            # inventing a transformation.
            raise ValueError(
                "STANDARDIZE_VALUE requires a "
                "governed replacement or "
                "transformation contract."
            )

        if remediation_action == "FLAG_FOR_REVIEW":
            # No mutation is being proposed.
            return current_value

        raise ValueError(
            "Unsupported remediation action."
        )

    # ============================================================
    # AI interpretation
    # ============================================================

    def _interpret_instruction(
        self,
        *,
        instruction: str,
        domain: str,
    ) -> ProfileRuleInterpretation:
        """
        Interpret steward intent.

        The deterministic parser below intentionally supports the
        first governed workflow while the existing LLM adapter is
        wired into this service.

        It is NOT a general natural-language execution engine.
        """

        deterministic = (
            self._try_deterministic_interpretation(
                instruction=instruction,
                domain=domain,
            )
        )

        if deterministic is not None:
            return deterministic

        if self.llm_service is None:
            raise ValueError(
                "The steward instruction could not "
                "be deterministically interpreted "
                "and AI interpretation is unavailable."
            )

        return self._interpret_with_llm(
            instruction=instruction,
            domain=domain,
        )

    def _try_deterministic_interpretation(
        self,
        *,
        instruction: str,
        domain: str,
    ) -> ProfileRuleInterpretation | None:
        """
        Narrow deterministic bootstrap for the first governed
        PRODUCT profile rule.

        Example:
        Delete product variant values containing "/".
        """

        normalized = instruction.strip()
        lowered = normalized.lower()

        product_variant_reference = (
            "product variant" in lowered
            or "product_variant" in lowered
            or "unit of measurement" in lowered
            or "uom" in lowered
        )
        normalized_instruction = str(
            instruction or ""
        ).strip().lower()

        delete_intent = (
            "delete" in lowered
            or "remove" in lowered
            or "clear" in lowered
            or "set to null" in lowered
        )
        quoted_value = self._extract_quoted_value(instruction)

        equals_language = any(
            phrase in normalized_instruction
            for phrase in (
                "equal to",
                "equals",
                "exactly",
                "exact value",
            )
        )

        if (
            equals_language
            and quoted_value is not None
        ):
            return ProfileRuleInterpretation(
                field_name="product_variant",
                rule_name="Product Variant Invalid Exact Value",
                rule_type="INVALID_PATTERN",
                operator="EQUALS",
                comparison_value=quoted_value,
                remediation_action="DELETE_VALUE",
                replacement_value=None,
                explanation=(
                    "The steward identified an exact "
                    "product_variant value as invalid and requested "
                    "that the governed field value be cleared. "
                    "The rule is scoped to this profile and requires "
                    "steward approval before activation."
                ),
            )

        contains_intent = (
            "contain" in lowered
            or "containing" in lowered
            or "contains" in lowered
        )

        if (
            domain == "PRODUCT"
            and product_variant_reference
            and delete_intent
            and contains_intent
        ):
            comparison_value = (
                self._extract_quoted_value(
                    normalized
                )
            )

            if comparison_value is None:
                return None

            return ProfileRuleInterpretation(
                field_name="product_variant",
                rule_name=(
                    "Product Variant Invalid "
                    "Value Removal"
                ),
                rule_type="INVALID_PATTERN",
                operator="CONTAINS",
                comparison_value=(
                    comparison_value
                ),
                remediation_action=(
                    "DELETE_VALUE"
                ),
                replacement_value=None,
                explanation=(
                    "The steward requested that "
                    "product variant values "
                    f"containing {comparison_value!r} "
                    "be cleared. This candidate is "
                    "scoped only to the governed "
                    "profile and requires preview "
                    "and steward approval."
                ),
            )

        return None

    def _interpret_with_llm(
        self,
        *,
        instruction: str,
        domain: str,
    ) -> ProfileRuleInterpretation:
        """
        LLM adapter boundary.

        We intentionally fail closed until this method is connected
        to the application's existing Claude service using its exact
        invocation contract.
        """

        raise NotImplementedError(
            "Profile rule AI interpretation has not "
            "yet been connected to the governed LLM "
            "adapter."
        )

    # ============================================================
    # Interpretation validation
    # ============================================================

    @staticmethod
    def _validate_interpretation(
        interpretation: ProfileRuleInterpretation,
    ) -> None:
        field_name = str(
            interpretation.field_name or ""
        ).strip()

        if not field_name:
            raise ValueError(
                "AI profile rule interpretation "
                "did not identify a field."
            )

        operator = str(
            interpretation.operator or ""
        ).strip().upper()

        if operator not in SUPPORTED_OPERATORS:
            raise ValueError(
                "AI profile rule interpretation "
                "returned an unsupported operator."
            )

        action = str(
            interpretation.remediation_action
            or ""
        ).strip().upper()

        if (
            action
            not in SUPPORTED_REMEDIATION_ACTIONS
        ):
            raise ValueError(
                "AI profile rule interpretation "
                "returned an unsupported "
                "remediation action."
            )

        if (
            operator
            in {
                "CONTAINS",
                "EQUALS",
                "REGEX",
                "IN",
            }
            and interpretation.comparison_value
            is None
        ):
            raise ValueError(
                "Profile rule operator requires "
                "a comparison value."
            )

        if (
            action == "DELETE_VALUE"
            and interpretation.replacement_value
            is not None
        ):
            raise ValueError(
                "DELETE_VALUE must resolve the "
                "targeted field value to NULL."
            )

        if (
            action == "REPLACE_VALUE"
            and interpretation.replacement_value
            is None
        ):
            raise ValueError(
                "REPLACE_VALUE requires a "
                "replacement value."
            )

        if operator == "REGEX":
            try:
                re.compile(
                    str(
                        interpretation
                        .comparison_value
                    )
                )
            except re.error as exc:
                raise ValueError(
                    "AI profile rule interpretation "
                    "returned an invalid REGEX."
                ) from exc

    # ============================================================
    # Serialization helpers
    # ============================================================

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _from_json(
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Persisted profile rule JSON "
                "could not be decoded."
            ) from exc

    @staticmethod
    def _extract_quoted_value(
        instruction: str,
    ) -> str | None:
        """
        Extract a simple steward-supplied quoted comparison value.

        Supports:
            containing "/"
            containing '/'
        """

        match = re.search(
            r"""["']([^"']+)["']""",
            instruction,
        )

        if not match:
            return None

        value = match.group(1)

        return value if value else None

    @staticmethod
    def _utc_now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)
