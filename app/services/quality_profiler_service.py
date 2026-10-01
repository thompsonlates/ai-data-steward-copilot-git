from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from google.cloud import bigquery

from app.services.universal_profile_rules_engine import (
    UniversalProfileRuleResult,
    UniversalProfileRulesEngine,
)


logger = logging.getLogger(__name__)


VALID_DIMENSIONS = {
    "COMPLETENESS",
    "VALIDITY",
    "UNIQUENESS",
    "STANDARDIZATION",
    "CONSISTENCY",
}

VALID_SEVERITIES = {
    "LOW",
    "MEDIUM",
    "HIGH",
    "CRITICAL",
}

@dataclass
class QualityFinding:
    finding_id: str
    organization_id: str
    profile_run_id: str
    domain: str
    source_table: str
    source_row_id: str
    record_id: str
    field_name: Optional[str]
    rule_id: str
    dimension: str
    severity: str
    finding_message: str
    observed_value: Optional[str] = None
    proposed_value: Optional[str] = None

@dataclass(frozen=True)
class QualityFieldConfig:
    field_name: str

    required: bool = False

    uniqueness_key: bool = False

    regex_pattern: Optional[str] = None

    allowed_values: Sequence[str] = field(
        default_factory=tuple
    )

    allowed_uom_values: Sequence[str] = field(
        default_factory=tuple
    )

    standardization_required: bool = False

    weight: float = 1.0

    severity: str = "MEDIUM"


@dataclass(frozen=True)
class CrossFieldRule:
    rule_id: str

    sql_condition: str

    description: str

    severity: str = "MEDIUM"


@dataclass(frozen=True)
class CompositeUniquenessRule:
    """
    A generic dataset-level uniqueness rule spanning two or more fields.

    Example:
        (product_id, gtin) must be unique when both values are present.
    """

    rule_id: str
    fields: Sequence[str]
    description: str
    severity: str = "HIGH"
    weight: float = 1.5


@dataclass(frozen=True)
class MappingCollisionRule:
    """
    A generic relationship rule that detects one identifier mapping to
    multiple identifiers in another field.

    Example:
        one GTIN should not map to multiple product_id values.
    """

    rule_id: str
    key_field: str
    mapped_field: str
    description: str
    severity: str = "CRITICAL"
    weight: float = 2.0


@dataclass(frozen=True)
class QualityProfileConfig:
    domain: str

    source_table: str

    business_key_field: str

    fields: Sequence[QualityFieldConfig]

    cross_field_rules: Sequence[CrossFieldRule] = field(
        default_factory=tuple
    )

    composite_uniqueness_rules: Sequence[
        CompositeUniquenessRule
    ] = field(default_factory=tuple)

    mapping_collision_rules: Sequence[
        MappingCollisionRule
    ] = field(default_factory=tuple)

    minimum_record_score: float = 80.0

    sample_limit: Optional[int] = None


@dataclass
class RecordQualityScore:
    organization_id: str
    profile_run_id: str
    domain: str
    source_table: str
    source_row_id: str
    record_id: str

    completeness_score: float
    validity_score: float
    uniqueness_score: float
    standardization_score: float
    consistency_score: float
    overall_score: float

    issue_count: int
    critical_issue_count: int
    high_issue_count: int
    medium_issue_count: int
    low_issue_count: int


@dataclass
class QualityRuleExecution:
    organization_id: str
    profile_run_id: str
    domain: str
    source_table: str

    rule_id: str
    dimension: str
    severity: str

    evaluated_record_count: int
    passed_record_count: int
    failed_record_count: int
    skipped_record_count: int

    execution_status: str
    compliance_rate: Optional[float]


@dataclass
class QualityProfileResult:
    organization_id: str

    profile_run_id: str

    domain: str

    source_table: str

    total_records: int

    scored_record_count: int

    avg_record_score: float

    avg_completeness_score: float

    avg_validity_score: float

    avg_uniqueness_score: float

    avg_standardization_score: float

    avg_consistency_score: float

    duplicate_record_count: int

    records_below_threshold: int

    records_with_findings: int

    total_findings: int

    critical_findings: int

    high_findings: int

    medium_findings: int

    low_findings: int

    findings: List[QualityFinding]

    record_scores: List[RecordQualityScore]

    rule_executions: List[QualityRuleExecution]

    generated_at: datetime

    # Phase 1 Universal Rules integration is intentionally shadow-only.
    # Existing findings, scores, duplicate counts, and rule executions remain
    # authoritative until regression testing is complete.
    universal_rules_shadow_enabled: bool = False
    universal_rules_shadow: Optional[Dict[str, Any]] = None




class QualityProfilerService:
    """
    Deterministic, tenant-aware data quality profiler.

    Responsibilities:
    - inspect connected/raw source data
    - evaluate deterministic DQ rules
    - calculate per-record DQ dimension scores
    - create explainable findings
    - calculate aggregate DQ posture

    This service DOES NOT call an LLM.

    Claude / AI rule recommendations should consume the resulting
    metrics through quality_intelligence_service.py.
    """

    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
        project_id: Optional[str] = None,
        universal_rules_engine: UniversalProfileRulesEngine | None = None,
        enable_universal_rules_shadow: Optional[bool] = None,
    ) -> None:
        self.client = client or bigquery.Client(
            project=project_id
        )

        self.universal_rules_engine = (
            universal_rules_engine
            or UniversalProfileRulesEngine()
        )

        if enable_universal_rules_shadow is None:
            enable_universal_rules_shadow = str(
                os.getenv(
                    "ADMS_UNIVERSAL_PROFILE_RULES_SHADOW",
                    "false",
                )
            ).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        self.enable_universal_rules_shadow = bool(
            enable_universal_rules_shadow
        )

    # ---------------------------------------------------------
    # Tenant safety
    # ---------------------------------------------------------

    @staticmethod
    def _normalize_identifier(
        value: Any,
    ) -> str:
        if value is None:
            return ""

        if isinstance(value, float) and value.is_integer():
            return str(int(value))

        if isinstance(value, int):
            return str(value)

        return str(value).strip()

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
                "tenant-isolated DQ profiling."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the "
                "org_ identifier standard."
            )

        return normalized

    # ---------------------------------------------------------
    # Identifier safety
    # ---------------------------------------------------------

    @staticmethod
    def _validate_table_id(
        table_id: str,
    ) -> str:
        normalized = str(
            table_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "source_table is required."
            )

        pattern = re.compile(
            r"^[A-Za-z0-9_\-]+\."
            r"[A-Za-z0-9_\-]+\."
            r"[A-Za-z0-9_\-]+$"
        )

        if not pattern.match(normalized):
            raise ValueError(
                "source_table must use "
                "project.dataset.table format."
            )

        return normalized

    @staticmethod
    def _validate_field_name(
        field_name: str,
    ) -> str:
        normalized = str(
            field_name or ""
        ).strip()

        if not re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*$",
            normalized,
        ):
            raise ValueError(
                f"Invalid field name: {field_name}"
            )

        return normalized

    # ---------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------

    def profile_table(
        self,
        *,
        organization_id: str,
        config: QualityProfileConfig,
    ) -> QualityProfileResult:
        """
        Profile a BigQuery table.

        This method only handles BigQuery extraction.
        All DQ evaluation is delegated to profile_rows().
        """

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        source_table = self._validate_table_id(
            config.source_table
        )

        business_key_field = (
            self._validate_field_name(
                config.business_key_field
            )
        )

        self._validate_config(
            config
        )

        logger.info(
            "Loading BigQuery source for DQ profiling. "
            "organization_id=%s "
            "domain=%s "
            "source_table=%s",
            effective_organization_id,
            config.domain,
            source_table,
        )

        rows = self._load_source_rows(
            source_table=source_table,
            business_key_field=business_key_field,
            config=config,
        )

        return self.profile_rows(
            organization_id=effective_organization_id,
            rows=rows,
            config=config,
            source_name=source_table,
    )

    @staticmethod
    def _is_valid_npi(
        value: str,
    ) -> bool:
        """
        Validate a 10-digit U.S. NPI using the CMS
        Luhn check-digit method with prefix 80840.
        """

        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if len(digits) != 10:
            return False

        # Reject obvious placeholders.
        if len(set(digits)) == 1:
            return False

        base = "80840" + digits[:9]

        total = 0

        # Luhn: process from right to left.
        reverse_digits = base[::-1]

        for index, char in enumerate(reverse_digits):
            number = int(char)

            # Double every second digit, starting
            # with the rightmost digit of base.
            if index % 2 == 0:
                number *= 2

                if number > 9:
                    number -= 9

            total += number

        expected_check_digit = (
            10 - (total % 10)
        ) % 10

        return expected_check_digit == int(
            digits[-1]
        )

    @staticmethod
    def _is_valid_gtin(
        value: str,
    ) -> bool:
        """
        Validate GTIN-8, GTIN-12 (UPC-A), GTIN-13,
        or GTIN-14 using the standard modulo-10 check digit.
        """

        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if len(digits) not in {
            8,
            12,
            13,
            14,
        }:
            return False

        # Reject obvious placeholder values.
        if len(set(digits)) == 1:
            return False

        body = digits[:-1]
        check_digit = int(digits[-1])

        total = 0

        # GTIN weighting is calculated from right to left
        # across the body digits: 3, 1, 3, 1, ...
        for index, char in enumerate(
            reversed(body)
        ):
            digit = int(char)

            weight = (
                3
                if index % 2 == 0
                else 1
            )

            total += digit * weight

        expected_check_digit = (
            10 - (total % 10)
        ) % 10

        return (
            expected_check_digit
            == check_digit
        )

    @staticmethod
    def _is_valid_aba_routing_number(
        value: str,
    ) -> bool:
        """
        Validate a 9-digit U.S. ABA routing number
        using the standard 3-7-1 checksum.
        """

        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if len(digits) != 9:
            return False

        # Reject obvious placeholder values.
        if len(set(digits)) == 1:
            return False

        weights = (
            3, 7, 1,
            3, 7, 1,
            3, 7, 1,
        )

        checksum = sum(
            int(digit) * weight
            for digit, weight
            in zip(digits, weights)
        )

        return checksum % 10 == 0

    @staticmethod
    def _parse_composite_unique_fields(
        condition: str,
    ) -> tuple[str, ...]:
        prefix = "COMPOSITE_UNIQUE:"
        normalized = str(condition or "").strip()

        if not normalized.upper().startswith(prefix):
            return tuple()

        raw_fields = normalized[len(prefix):]
        fields = tuple(
            str(item or "").strip().lower()
            for item in raw_fields.split(",")
            if str(item or "").strip()
        )

        return fields if len(fields) >= 2 else tuple()

    @classmethod
    def _find_cross_field_composite_duplicate_keys(
        cls,
        *,
        rows: Sequence[Dict[str, Any]],
        rules: Sequence[CrossFieldRule],
    ) -> Dict[str, set[tuple[str, ...]]]:
        results: Dict[str, set[tuple[str, ...]]] = {}

        for rule in rules:
            fields = cls._parse_composite_unique_fields(
                rule.sql_condition
            )

            if not fields:
                continue

            counts: Dict[tuple[str, ...], int] = {}

            for row in rows:
                key = tuple(
                    cls._normalize_identifier(
                        row.get(field_name)
                    )
                    for field_name in fields
                )

                if not key or any(not value for value in key):
                    continue

                counts[key] = counts.get(key, 0) + 1

            results[rule.rule_id] = {
                key
                for key, count in counts.items()
                if count > 1
            }

        return results

    @staticmethod
    def _register_rule_execution(
        state: Dict[str, Dict[str, Any]],
        *,
        rule_id: str,
        dimension: str,
        severity: str,
        not_implemented: bool = False,
    ) -> None:
        normalized_rule_id = str(rule_id or "").strip().upper()

        if not normalized_rule_id:
            return

        safe_dimension = str(dimension or "VALIDITY").strip().upper()
        safe_severity = str(severity or "MEDIUM").strip().upper()

        if safe_dimension not in VALID_DIMENSIONS:
            safe_dimension = "VALIDITY"

        if safe_severity not in VALID_SEVERITIES:
            safe_severity = "MEDIUM"

        existing = state.setdefault(
            normalized_rule_id,
            {
                "rule_id": normalized_rule_id,
                "dimension": safe_dimension,
                "severity": safe_severity,
                "evaluated": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "not_implemented": False,
            },
        )

        if not_implemented:
            existing["not_implemented"] = True

    @classmethod
    def _initialize_rule_execution_state(
        cls,
        *,
        config: QualityProfileConfig,
        domain: str,
    ) -> Dict[str, Dict[str, Any]]:
        state: Dict[str, Dict[str, Any]] = {}
        normalized_domain = str(domain or "").strip().upper()
        junk_text_fields = {
            "full_name",
            "first_name",
            "last_name",
            "supplier_name",
            "product_name",
            "institution_name",
            "specialty",
            "address",
        }

        for field_config in config.fields:
            field_name = str(field_config.field_name).strip().lower()
            prefix = field_name.upper()
            severity = field_config.severity

            if field_config.required:
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_REQUIRED",
                    dimension="COMPLETENESS",
                    severity=severity,
                )

            if field_config.regex_pattern:
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_FORMAT_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if field_config.allowed_values:
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_ALLOWED_VALUE",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if field_config.standardization_required:
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_STANDARDIZATION",
                    dimension="STANDARDIZATION",
                    severity="LOW",
                )

            if (
                field_config.uniqueness_key
                and field_name == config.business_key_field
            ):
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_UNIQUENESS",
                    dimension="UNIQUENESS",
                    severity="HIGH",
                )

            if field_name == "phone_number" or field_name in junk_text_fields:
                cls._register_rule_execution(
                    state,
                    rule_id=f"{prefix}_JUNK_VALUE",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if normalized_domain == "BANKING" and field_name == "routing_number":
                cls._register_rule_execution(
                    state,
                    rule_id="ROUTING_NUMBER_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="CRITICAL",
                )

            if normalized_domain == "PRODUCT" and field_name == "effective_lot_date":
                cls._register_rule_execution(
                    state,
                    rule_id="EFFECTIVE_LOT_DATE_DATE_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if normalized_domain in {"CUSTOMER", "PATIENT"} and field_name == "dob":
                cls._register_rule_execution(
                    state,
                    rule_id="DOB_DATE_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if normalized_domain == "PROVIDER" and field_name == "npi":
                cls._register_rule_execution(
                    state,
                    rule_id="NPI_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="CRITICAL",
                )

            if field_name == "phone_number":
                cls._register_rule_execution(
                    state,
                    rule_id="PHONE_NUMBER_SEMANTIC_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

            if normalized_domain == "PRODUCT" and field_name == "gtin":
                cls._register_rule_execution(
                    state,
                    rule_id="GTIN_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="HIGH",
                )

            if (
                normalized_domain == "PRODUCT"
                and field_name == "product_variant"
                and field_config.allowed_uom_values
            ):
                cls._register_rule_execution(
                    state,
                    rule_id="PRODUCT_VARIANT_UOM_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

                # Authoritative governance rule.
                #
                # This is the single policy-facing Product Variant control.
                # A populated Product Variant passes only when it has:
                #   quantity + exactly one space + approved UOM
                #
                # Diagnostic rules such as FORMAT_VALIDITY,
                # UOM_VALIDITY, and STANDARDIZATION remain available
                # to explain why the authoritative control failed.
                cls._register_rule_execution(
                    state,
                    rule_id="PRODUCT_VARIANT_VALIDITY",
                    dimension="VALIDITY",
                    severity=severity,
                )

        for rule in config.composite_uniqueness_rules:
            cls._register_rule_execution(
                state,
                rule_id=rule.rule_id,
                dimension="UNIQUENESS",
                severity=rule.severity,
            )

        for rule in config.mapping_collision_rules:
            cls._register_rule_execution(
                state,
                rule_id=rule.rule_id,
                dimension="CONSISTENCY",
                severity=rule.severity,
            )

        for rule in config.cross_field_rules:
            supported = bool(
                cls._parse_composite_unique_fields(
                    rule.sql_condition
                )
            )
            cls._register_rule_execution(
                state,
                rule_id=rule.rule_id,
                dimension="CONSISTENCY",
                severity=rule.severity,
                not_implemented=not supported,
            )

        return state

    @classmethod
    def _observe_rule_execution(
        cls,
        state: Dict[str, Dict[str, Any]],
        *,
        rule_id: str,
        dimension: str,
        severity: str,
        outcome: str,
    ) -> None:
        cls._register_rule_execution(
            state,
            rule_id=rule_id,
            dimension=dimension,
            severity=severity,
        )

        item = state[str(rule_id).strip().upper()]
        normalized_outcome = str(outcome or "").strip().upper()

        if normalized_outcome == "PASS":
            item["evaluated"] += 1
            item["passed"] += 1
        elif normalized_outcome == "FAIL":
            item["evaluated"] += 1
            item["failed"] += 1
        elif normalized_outcome == "SKIP":
            item["skipped"] += 1
        else:
            raise ValueError(
                f"Unsupported rule execution outcome: {outcome}"
            )

    @staticmethod
    def _finalize_rule_executions(
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_table: str,
        state: Dict[str, Dict[str, Any]],
    ) -> List[QualityRuleExecution]:
        results: List[QualityRuleExecution] = []

        for rule_id in sorted(state):
            item = state[rule_id]
            evaluated = int(item.get("evaluated") or 0)
            passed = int(item.get("passed") or 0)
            failed = int(item.get("failed") or 0)
            skipped = int(item.get("skipped") or 0)
            not_implemented = bool(item.get("not_implemented"))

            if not_implemented:
                execution_status = "NOT_IMPLEMENTED"
                compliance_rate = None
            elif evaluated > 0:
                execution_status = "EXECUTED"
                compliance_rate = passed / evaluated
            elif skipped > 0:
                execution_status = "NOT_APPLICABLE"
                compliance_rate = None
            else:
                execution_status = "NOT_EVALUATED"
                compliance_rate = None

            results.append(
                QualityRuleExecution(
                    organization_id=organization_id,
                    profile_run_id=profile_run_id,
                    domain=domain,
                    source_table=source_table,
                    rule_id=rule_id,
                    dimension=str(item["dimension"]),
                    severity=str(item["severity"]),
                    evaluated_record_count=evaluated,
                    passed_record_count=passed,
                    failed_record_count=failed,
                    skipped_record_count=skipped,
                    execution_status=execution_status,
                    compliance_rate=(
                        round(compliance_rate, 6)
                        if compliance_rate is not None
                        else None
                    ),
                )
            )

        return results

    def profile_rows(
        self,
        *,
        organization_id: str,
        rows: Sequence[Dict[str, Any]],
        config: QualityProfileConfig,
        source_name: str,
    ) -> QualityProfileResult:
        """
        Profile normalized tabular rows from any connector.

        Supported sources can include:
        - Google BigQuery
        - Google Sheets
        - CSV
        - Snowflake
        - Databricks
        - SQL Server
        - future tabular connectors

        The connector is responsible for returning:
            list[dict[str, Any]]

        This method owns all deterministic DQ evaluation so profiling
        logic is never duplicated across connectors.
        """

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        normalized_domain = str(
            config.domain or ""
        ).strip().upper()

        if not normalized_domain:
            raise ValueError(
                "domain is required."
            )

        normalized_source_name = str(
            source_name or ""
        ).strip()

        if not normalized_source_name:
            raise ValueError(
                "source_name is required."
            )

        business_key_field = (
            self._validate_field_name(
                config.business_key_field
            )
        )

        self._validate_config(
            config
        )

        normalized_rows = (
            self._normalize_input_rows(
                rows
            )
        )

        profile_run_id = (
            f"dqp_{uuid.uuid4().hex[:20]}"
        )

        logger.info(
            "Starting DQ profiling. "
            "organization_id=%s "
            "profile_run_id=%s "
            "domain=%s "
            "source=%s "
            "record_count=%s",
            effective_organization_id,
            profile_run_id,
            normalized_domain,
            normalized_source_name,
            len(normalized_rows),
        )

        business_key_is_unique = any(
            field_config.field_name == business_key_field
            and field_config.uniqueness_key
            for field_config in config.fields
        )

        duplicate_keys = (
            self._find_duplicate_business_keys(
                rows=normalized_rows,
                business_key_field=business_key_field,
            )
            if business_key_is_unique
            else set()
        )

        composite_duplicate_keys = (
            self._find_composite_duplicate_keys(
                rows=normalized_rows,
                rules=config.composite_uniqueness_rules,
            )
        )

        mapping_collision_keys = (
            self._find_mapping_collisions(
                rows=normalized_rows,
                rules=config.mapping_collision_rules,
            )
        )

        cross_field_composite_duplicate_keys = (
            self._find_cross_field_composite_duplicate_keys(
                rows=normalized_rows,
                rules=config.cross_field_rules,
            )
        )

        rule_execution_state = (
            self._initialize_rule_execution_state(
                config=config,
                domain=normalized_domain,
            )
        )

        findings: List[QualityFinding] = []
        scores: List[RecordQualityScore] = []

        for row_index, row in enumerate(
            normalized_rows,
            start=1,
        ):
            source_row_id = (
                f"{profile_run_id}_ROW_{row_index}"
            )

            record_id = (
                self._normalize_identifier(
                    row.get(
                        business_key_field
                    )
                )
            )

            if not record_id:
                record_id = (
                    self._build_missing_record_id(
                        profile_run_id=profile_run_id,
                        row_index=row_index,
                    )
                )

            (
                row_findings,
                row_score,
            ) = self._profile_record(
                organization_id=effective_organization_id,
                profile_run_id=profile_run_id,
                domain=normalized_domain,
                source_table=normalized_source_name,
                source_row_id=source_row_id,
                record_id=record_id,
                row=row,
                config=config,
                duplicate_keys=duplicate_keys,
                composite_duplicate_keys=(
                    composite_duplicate_keys
                ),
                mapping_collision_keys=(
                    mapping_collision_keys
                ),
                cross_field_composite_duplicate_keys=(
                    cross_field_composite_duplicate_keys
                ),
                rule_execution_state=(
                    rule_execution_state
                ),
            )

            findings.extend(
                row_findings
            )

            scores.append(
                row_score
            )

        rule_executions = (
            self._finalize_rule_executions(
                organization_id=effective_organization_id,
                profile_run_id=profile_run_id,
                domain=normalized_domain,
                source_table=normalized_source_name,
                state=rule_execution_state,
            )
        )

        result = self._build_result(
            organization_id=effective_organization_id,
            profile_run_id=profile_run_id,
            domain=normalized_domain,
            source_table=normalized_source_name,
            minimum_record_score=(
                config.minimum_record_score
            ),
            findings=findings,
            scores=scores,
            rule_executions=rule_executions,
            total_records=len(
                normalized_rows
            ),
            duplicate_record_count=(
                self._count_duplicate_identity_records(
                    rows=normalized_rows,
                    business_key_field=(
                        business_key_field
                    ),
                    duplicate_keys=duplicate_keys,
                    composite_rules=(
                        config.composite_uniqueness_rules
                    ),
                    composite_duplicate_keys=(
                        composite_duplicate_keys
                    ),
                )
            ),
        )

        # -----------------------------------------------------
        # Universal Profile Rules - Phase 1 shadow integration
        # -----------------------------------------------------
        #
        # Shadow mode is deliberately non-authoritative:
        # - it does not change existing QualityFinding rows
        # - it does not change RecordQualityScore calculations
        # - it does not change duplicate counts
        # - it does not change existing QualityRuleExecution rows
        # - it does not persist or activate rules
        #
        # This lets us compare the new governed rule architecture against
        # the mature profiler before promoting any universal rule into the
        # production DQ scoring path.
        result.universal_rules_shadow_enabled = (
            self.enable_universal_rules_shadow
        )

        if self.enable_universal_rules_shadow:
            universal_result = (
                self._evaluate_universal_rules_shadow(
                    organization_id=effective_organization_id,
                    profile_run_id=profile_run_id,
                    domain=normalized_domain,
                    source_name=normalized_source_name,
                    business_key_field=business_key_field,
                    rows=normalized_rows,
                    config=config,
                )
            )

            result.universal_rules_shadow = (
                self._serialize_universal_rules_shadow(
                    universal_result
                )
            )

            logger.info(
                "Universal Profile Rules shadow evaluation complete. "
                "organization_id=%s profile_run_id=%s domain=%s "
                "resolved_rules=%s executed_rules=%s findings=%s",
                effective_organization_id,
                profile_run_id,
                normalized_domain,
                universal_result.resolved_rule_count,
                universal_result.executed_rule_count,
                universal_result.total_findings,
            )

        return result

    def _evaluate_universal_rules_shadow(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_name: str,
        business_key_field: str,
        rows: Sequence[Dict[str, Any]],
        config: QualityProfileConfig,
    ) -> UniversalProfileRuleResult:
        """
        Run ACTIVE governed universal/semantic rules without affecting the
        existing profiler's authoritative findings or scoring.

        Mapped fields come from the existing QualityProfileConfig plus the
        business key so the new engine evaluates the same logical dataset.
        """

        mapped_fields = {
            str(field_config.field_name).strip().lower()
            for field_config in config.fields
            if str(field_config.field_name or "").strip()
        }
        mapped_fields.add(
            str(business_key_field).strip().lower()
        )

        return self.universal_rules_engine.evaluate_profile(
            organization_id=organization_id,
            domain=domain,
            profile_run_id=profile_run_id,
            rows=rows,
            source_name=source_name,
            business_key_field=business_key_field,
            mapped_fields=tuple(
                sorted(mapped_fields)
            ),
        )

    @staticmethod
    def _serialize_universal_rules_shadow(
        universal_result: UniversalProfileRuleResult,
    ) -> Dict[str, Any]:
        """
        Return a JSON-safe diagnostic payload for backend regression testing.

        We intentionally expose evidence, not governance mutations. The
        existing API can decide later whether/how to surface this in the UI.
        """

        semantic_types: Dict[str, Dict[str, Any]] = {}

        for field_name, inference in (
            universal_result.semantic_inferences.items()
        ):
            semantic_type = getattr(
                inference,
                "semantic_type",
                None,
            )
            semantic_types[field_name] = {
                "semantic_type": (
                    getattr(semantic_type, "value", semantic_type)
                ),
                "confidence": getattr(
                    inference,
                    "confidence",
                    None,
                ),
                "source": (
                    getattr(
                        getattr(inference, "source", None),
                        "value",
                        getattr(inference, "source", None),
                    )
                ),
                "requires_review": getattr(
                    inference,
                    "requires_review",
                    None,
                ),
            }

        return {
            "mode": "SHADOW",
            "authoritative": False,
            "profile_run_id": universal_result.profile_run_id,
            "domain": universal_result.domain,
            "source_name": universal_result.source_name,
            "total_records": universal_result.total_records,
            "resolved_rule_count": (
                universal_result.resolved_rule_count
            ),
            "executed_rule_count": (
                universal_result.executed_rule_count
            ),
            "total_findings": universal_result.total_findings,
            "semantic_types": semantic_types,
            "findings": [
                {
                    "rule_id": item.rule_id,
                    "rule_version": item.rule_version,
                    "logical_key": item.logical_key,
                    "rule_family": item.rule_family,
                    "evaluator": item.evaluator,
                    "severity": item.severity,
                    "field_name": item.field_name,
                    "semantic_type": item.semantic_type,
                    "source_row_id": item.source_row_id,
                    "record_id": item.record_id,
                    "finding_message": item.finding_message,
                    "observed_value": item.observed_value,
                    "proposed_value": item.proposed_value,
                    "deterministic": item.deterministic,
                    "remediation_available": (
                        item.remediation_available
                    ),
                    "remediation_type": item.remediation_type,
                }
                for item in universal_result.findings
            ],
            "rule_executions": [
                {
                    "rule_id": item.rule_id,
                    "rule_version": item.rule_version,
                    "logical_key": item.logical_key,
                    "rule_family": item.rule_family,
                    "evaluator": item.evaluator,
                    "severity": item.severity,
                    "field_name": item.field_name,
                    "semantic_type": item.semantic_type,
                    "evaluated_record_count": (
                        item.evaluated_record_count
                    ),
                    "passed_record_count": (
                        item.passed_record_count
                    ),
                    "failed_record_count": (
                        item.failed_record_count
                    ),
                    "skipped_record_count": (
                        item.skipped_record_count
                    ),
                    "execution_status": item.execution_status,
                    "compliance_rate": item.compliance_rate,
                    "deterministic": item.deterministic,
                }
                for item in universal_result.rule_executions
            ],
        }

    # ---------------------------------------------------------
    # Configuration validation
    # ---------------------------------------------------------

    def _validate_config(
        self,
        config: QualityProfileConfig,
    ) -> None:

        if not config.fields:
            raise ValueError(
                "At least one profile field "
                "must be configured."
            )

        for field_config in config.fields:
            self._validate_field_name(
                field_config.field_name
            )

            if field_config.weight <= 0:
                raise ValueError(
                    "DQ field weight must "
                    "be greater than zero."
                )

            severity = str(
                field_config.severity
            ).upper()

            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity: "
                    f"{severity}"
                )

        for rule in config.composite_uniqueness_rules:
            if not rule.rule_id:
                raise ValueError(
                    "Composite uniqueness rule_id is required."
                )

            if len(tuple(rule.fields)) < 2:
                raise ValueError(
                    "Composite uniqueness rules require "
                    "at least two fields."
                )

            for field_name in rule.fields:
                self._validate_field_name(field_name)

            if str(rule.severity).upper() not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity: {rule.severity}"
                )

            if rule.weight <= 0:
                raise ValueError(
                    "Composite uniqueness rule weight must "
                    "be greater than zero."
                )

        for rule in config.mapping_collision_rules:
            if not rule.rule_id:
                raise ValueError(
                    "Mapping collision rule_id is required."
                )

            self._validate_field_name(rule.key_field)
            self._validate_field_name(rule.mapped_field)

            if rule.key_field == rule.mapped_field:
                raise ValueError(
                    "Mapping collision rule fields must differ."
                )

            if str(rule.severity).upper() not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity: {rule.severity}"
                )

            if rule.weight <= 0:
                raise ValueError(
                    "Mapping collision rule weight must "
                    "be greater than zero."
                )

        if not (
            0
            <= config.minimum_record_score
            <= 100
        ):
            raise ValueError(
                "minimum_record_score must "
                "be between 0 and 100."
            )

    # ---------------------------------------------------------
    # BigQuery extraction
    # ---------------------------------------------------------

    def _load_source_rows(
        self,
        *,
        source_table: str,
        business_key_field: str,
        config: QualityProfileConfig,
    ) -> List[Dict[str, Any]]:

        selected_fields = {
            business_key_field
        }

        selected_fields.update(
            self._validate_field_name(
                field.field_name
            )
            for field in config.fields
        )

        for rule in config.composite_uniqueness_rules:
            selected_fields.update(
                self._validate_field_name(field_name)
                for field_name in rule.fields
            )

        for rule in config.mapping_collision_rules:
            selected_fields.add(
                self._validate_field_name(rule.key_field)
            )
            selected_fields.add(
                self._validate_field_name(rule.mapped_field)
            )

        field_list = ",\n".join(
            f"`{field_name}`"
            for field_name
            in sorted(selected_fields)
        )

        limit_clause = ""

        if config.sample_limit:
            safe_limit = max(
                1,
                min(
                    int(config.sample_limit),
                    1_000_000,
                ),
            )

            limit_clause = (
                f"\nLIMIT {safe_limit}"
            )

        sql = f"""
        SELECT
          {field_list}
        FROM `{source_table}`
        {limit_clause}
        """

        return [
            dict(row.items())
            for row
            in self.client.query(sql).result()
        ]

    @staticmethod
    def _normalize_input_rows(
        rows: Sequence[
            Dict[str, Any]
        ],
    ) -> List[Dict[str, Any]]:
        """
        Normalize connector output into a stable list of dictionaries.
        """

        normalized_rows: List[
            Dict[str, Any]
        ] = []

        for row in rows:
            if row is None:
                continue

            if not isinstance(
                row,
                dict,
            ):
                try:
                    normalized_row = dict(
                        row
                    )
                except Exception as exc:
                    raise ValueError(
                        "Profiler input rows must "
                        "be dictionary-like objects."
                    ) from exc
            else:
                normalized_row = dict(
                    row
                )

            if not normalized_row:
                continue

            if all(
                value is None
                or (
                    isinstance(
                        value,
                        str,
                    )
                    and not value.strip()
                )
                for value
                in normalized_row.values()
            ):
                continue

            normalized_rows.append(
                normalized_row
            )

        return normalized_rows

    @staticmethod
    def _is_valid_date_value(
        value: str,
        *,
        allow_future: bool = True,
    ) -> bool:
        """
        Validate supported date formats using real calendar parsing.

        Supported:
        - YYYY-MM-DD
        - MM/DD/YYYY
        - YYYY/MM/DD

        When allow_future=False, future dates are rejected.
        """

        text = str(value or "").strip()

        if not text:
            return False

        parsed_date = None

        for fmt in (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%Y/%m/%d",
        ):
            try:
                parsed_date = datetime.strptime(
                    text,
                    fmt,
                ).date()
                break
            except ValueError:
                continue

        if parsed_date is None:
            return False

        if (
            not allow_future
            and parsed_date > datetime.now(
                timezone.utc
            ).date()
        ):
            return False

        return True

    @staticmethod
    def _is_junk_phone(
        value: str,
    ) -> bool:
        """
        Detect obvious placeholder or fabricated US phone values.
        """

        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if not digits:
            return False

        if (
            len(digits) == 11
            and digits.startswith("1")
        ):
            digits = digits[1:]

        if len(digits) != 10:
            return True

        # Same digit repeated: 0000000000, 9999999999, etc.
        if len(set(digits)) == 1:
            return True

        # Repeating two-digit patterns: 1212121212.
        if digits[:2] * 5 == digits:
            return True

        # Obvious sequential / placeholder values.
        if digits in {
            "0123456789",
            "1234567890",
            "9876543210",
        }:
            return True

        # NANP area code and central-office code cannot begin with 0 or 1.
       # Invalid / unusable area code.
        if digits[0] in {"0", "1"}:
            return True

        return False

    @staticmethod
    def _is_junk_text(
        value: str,
    ) -> bool:
        """
        Detect common placeholders and low-information repeated text.
        """

        text = str(value or "").strip()

        if not text:
            return False

        normalized = re.sub(
            r"[^A-Za-z0-9]",
            "",
            text,
        ).lower()

        if not normalized:
            return True

        if normalized in {
            "test",
            "testing",
            "dummy",
            "unknown",
            "na",
            "none",
            "null",
            "sample",
            "placeholder",
            "asdf",
            "qwerty",
            "xxxx",
            "xxxxxxxx",
        }:
            return True

        # One repeated character: aaaaaaaa, bbbbbbbb, 11111111.
        if (
            len(normalized) >= 6
            and len(set(normalized)) == 1
        ):
            return True

        # Very low character diversity: aaaaaabbbb, bbbbbbaaaa, etc.
        if len(normalized) >= 8:
            unique_ratio = (
                len(set(normalized))
                / len(normalized)
            )

            if unique_ratio <= 0.25:
                return True

        return False

    @staticmethod
    def _build_missing_record_id(
        *,
        profile_run_id: str,
        row_index: int,
    ) -> str:
        """
        Create a deterministic identifier within a profile run for records
        missing their configured business key.

        The missing business key itself will still generate a DQ finding.
        """

        return (
            f"MISSING_KEY_"
            f"{profile_run_id}_"
            f"{row_index}"
        )

    @staticmethod
    def _is_valid_phone_number(
        value: str,
    ) -> bool:
        """
        Validate a North American phone number after normalization.

        Rules:
        - optional leading country code 1
        - exactly 10 national digits
        - area code cannot start with 0 or 1
        - exchange code cannot start with 0 or 1
        - reject obvious placeholder/repeated values
        """

        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if (
            len(digits) == 11
            and digits.startswith("1")
        ):
            digits = digits[1:]

        if len(digits) != 10:
            return False

        # Obvious junk / placeholder patterns.
        if len(set(digits)) == 1:
            return False

        if digits in {
            "0123456789",
            "1234567890",
            "9876543210",
        }:
            return False

        # NANP area code: NXX, where N = 2-9
        area_code = digits[:3]

        # NANP central office / exchange code: NXX
        exchange_code = digits[3:6]

        if area_code[0] in {"0", "1"}:
            return False

        if exchange_code[0] in {"0", "1"}:
            return False

        return True

    # ---------------------------------------------------------
    # Duplicate analysis
    # ---------------------------------------------------------

    @staticmethod
    def _find_duplicate_business_keys(
        *,
        rows: Sequence[Dict[str, Any]],
        business_key_field: str,
    ) -> set[str]:

        counts: Dict[str, int] = {}

        for row in rows:
            key = QualityProfilerService._normalize_identifier(
                row.get(business_key_field)
            )

            if not key:
                continue

            counts[key] = counts.get(key, 0) + 1

        return {
            key
            for key, count in counts.items()
            if count > 1
        }

    @staticmethod
    def _find_composite_duplicate_keys(
        *,
        rows: Sequence[Dict[str, Any]],
        rules: Sequence[CompositeUniquenessRule],
    ) -> Dict[str, set[tuple[str, ...]]]:
        """
        Precompute duplicate composite identities once per profile.

        A rule is evaluated only when every component value is present.
        This is important for optional identifiers such as GTIN.
        """
        duplicate_keys: Dict[
            str,
            set[tuple[str, ...]],
        ] = {}

        for rule in rules:
            counts: Dict[tuple[str, ...], int] = {}

            for row in rows:
                key = tuple(
                    QualityProfilerService._normalize_identifier(
                        row.get(field_name)
                    )
                    for field_name in rule.fields
                )

                if not key or any(not value for value in key):
                    continue

                counts[key] = counts.get(key, 0) + 1

            duplicate_keys[rule.rule_id] = {
                key
                for key, count in counts.items()
                if count > 1
            }

        return duplicate_keys

    @staticmethod
    def _find_mapping_collisions(
        *,
        rows: Sequence[Dict[str, Any]],
        rules: Sequence[MappingCollisionRule],
    ) -> Dict[str, set[str]]:
        """
        Precompute identifier collisions once per profile.

        Example: if one GTIN maps to more than one product_id, the GTIN
        is returned as a collision key for that rule.
        """
        collisions: Dict[str, set[str]] = {}

        for rule in rules:
            mappings: Dict[str, set[str]] = {}

            for row in rows:
                key_value = (
                    QualityProfilerService._normalize_identifier(
                        row.get(rule.key_field)
                    )
                )
                mapped_value = (
                    QualityProfilerService._normalize_identifier(
                        row.get(rule.mapped_field)
                    )
                )

                if not key_value or not mapped_value:
                    continue

                mappings.setdefault(key_value, set()).add(
                    mapped_value
                )

            collisions[rule.rule_id] = {
                key_value
                for key_value, mapped_values in mappings.items()
                if len(mapped_values) > 1
            }

        return collisions

    @staticmethod
    def _count_duplicate_identity_records(
        *,
        rows: Sequence[Dict[str, Any]],
        business_key_field: str,
        duplicate_keys: set[str],
        composite_rules: Sequence[CompositeUniquenessRule],
        composite_duplicate_keys: Dict[
            str,
            set[tuple[str, ...]],
        ],
    ) -> int:
        """
        Count records participating in an actual configured uniqueness
        failure. Repeated non-unique business keys are intentionally not
        counted as duplicate records.
        """
        duplicate_row_indexes: set[int] = set()

        for row_index, row in enumerate(rows):
            business_key = (
                QualityProfilerService._normalize_identifier(
                    row.get(business_key_field)
                )
            )

            if business_key and business_key in duplicate_keys:
                duplicate_row_indexes.add(row_index)

            for rule in composite_rules:
                key = tuple(
                    QualityProfilerService._normalize_identifier(
                        row.get(field_name)
                    )
                    for field_name in rule.fields
                )

                if not key or any(not value for value in key):
                    continue

                if key in composite_duplicate_keys.get(
                    rule.rule_id,
                    set(),
                ):
                    duplicate_row_indexes.add(row_index)

        return len(duplicate_row_indexes)

    # ---------------------------------------------------------
    # Record profiling
    # ---------------------------------------------------------

    def _profile_record(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_table: str,
        source_row_id: str,
        record_id: str,
        row: Dict[str, Any],
        config: QualityProfileConfig,
        duplicate_keys: set[str],
        composite_duplicate_keys: Dict[
            str,
            set[tuple[str, ...]],
        ],
        mapping_collision_keys: Dict[str, set[str]],
        cross_field_composite_duplicate_keys: Dict[
            str,
            set[tuple[str, ...]],
        ],
        rule_execution_state: Dict[str, Dict[str, Any]],
    ) -> tuple[
        List[QualityFinding],
        RecordQualityScore,
    ]:

        findings: List[
            QualityFinding
        ] = []

        dimension_results: Dict[
            str,
            List[tuple[float, float]],
        ] = {
            "COMPLETENESS": [],
            "VALIDITY": [],
            "UNIQUENESS": [],
            "STANDARDIZATION": [],
            "CONSISTENCY": [],
        }

        for field_config in config.fields:
            field_name = (
                field_config.field_name
            )

            value = row.get(
                field_name
            )

            normalized_value = (
                self._normalize_value(
                    value
                )
            )

            weight = float(
                field_config.weight
            )

            # ------------------------------------------
            # Completeness
            # ------------------------------------------

            if field_config.required:
                passed = bool(
                    normalized_value
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_REQUIRED",
                    dimension="COMPLETENESS",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "COMPLETENESS"
                ].append(
                    (
                        100.0
                        if passed
                        else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                f"{field_name.upper()}"
                                "_REQUIRED"
                            ),
                            dimension="COMPLETENESS",
                            severity=(
                                field_config.severity
                            ),
                            message=(
                                f"{field_name} "
                                "is required but "
                                "is missing."
                            ),
                            observed_value=None,
                        )
                    )

            # ------------------------------------------
            # Validity - regex
            # ------------------------------------------

            if (
                not normalized_value
                and field_config.regex_pattern
            ):
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_FORMAT_VALIDITY",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="SKIP",
                )

            if (
                normalized_value
                and field_config.regex_pattern
            ):
                passed = bool(
                    re.fullmatch(
                        field_config.regex_pattern,
                        normalized_value,
                    )
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_FORMAT_VALIDITY",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                f"{field_name.upper()}"
                                "_FORMAT_VALIDITY"
                            ),
                            dimension="VALIDITY",
                            severity=field_config.severity,
                            message=(
                                f"{field_name} does not match "
                                "the approved format."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            if domain.upper() == "BANKING" and field_name == "routing_number" and (
                not normalized_value
                or not re.fullmatch(r"\d{9}", normalized_value)
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="ROUTING_NUMBER_CHECKSUM_VALIDITY",
                    dimension="VALIDITY", severity="CRITICAL", outcome="SKIP"
                )

            # ------------------------------------------
            # Banking - ABA routing checksum
            # ------------------------------------------

            if (
                domain.upper() == "BANKING"
                and field_name == "routing_number"
                and normalized_value
                and re.fullmatch(
                    r"\d{9}",
                    normalized_value,
                )
            ):
                passed = self._is_valid_aba_routing_number(
                    normalized_value
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="ROUTING_NUMBER_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="CRITICAL",
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                "ROUTING_NUMBER"
                                "_CHECKSUM_VALIDITY"
                            ),
                            dimension="VALIDITY",
                            severity="CRITICAL",
                            message=(
                                "routing_number failed the "
                                "ABA routing-number checksum "
                                "validation."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            if domain.upper() == "PRODUCT" and field_name == "effective_lot_date" and (
                not normalized_value
                or not re.fullmatch(
                    r"(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{4}/\d{2}/\d{2})",
                    normalized_value,
                )
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="EFFECTIVE_LOT_DATE_DATE_VALIDITY",
                    dimension="VALIDITY", severity=field_config.severity, outcome="SKIP"
                )

            # ------------------------------------------
            # Product - effective / lot date validity
            # ------------------------------------------

            if (
                domain.upper() == "PRODUCT"
                and field_name == "effective_lot_date"
                and normalized_value
                and re.fullmatch(
                    (
                        r"(?:\d{4}-\d{2}-\d{2}|"
                        r"\d{2}/\d{2}/\d{4}|"
                        r"\d{4}/\d{2}/\d{2})"
                    ),
                    normalized_value,
                )
            ):
                passed = self._is_valid_date_value(
                    normalized_value,
                    allow_future=True,
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="EFFECTIVE_LOT_DATE_DATE_VALIDITY",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                "EFFECTIVE_LOT_DATE"
                                "_DATE_VALIDITY"
                            ),
                            dimension="VALIDITY",
                            severity=field_config.severity,
                            message=(
                                "effective_lot_date is not "
                                "a valid calendar date."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            if domain.upper() in {"CUSTOMER", "PATIENT"} and field_name == "dob" and (
                not normalized_value
                or not re.fullmatch(
                    r"(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{4}/\d{2}/\d{2})",
                    normalized_value,
                )
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="DOB_DATE_VALIDITY",
                    dimension="VALIDITY", severity=field_config.severity, outcome="SKIP"
                )

            # ------------------------------------------
            # Customer / Patient - DOB validity
            # ------------------------------------------

            if (
                domain.upper() in {"CUSTOMER", "PATIENT"}
                and field_name == "dob"
                and normalized_value
                and re.fullmatch(
                    (
                        r"(?:\d{4}-\d{2}-\d{2}|"
                        r"\d{2}/\d{2}/\d{4}|"
                        r"\d{4}/\d{2}/\d{2})"
                    ),
                    normalized_value,
                )
            ):
                passed = self._is_valid_date_value(
                    normalized_value,
                    allow_future=False,
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="DOB_DATE_VALIDITY",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id="DOB_DATE_VALIDITY",
                            dimension="VALIDITY",
                            severity=field_config.severity,
                            message=(
                                "dob is not a valid calendar "
                                "date or is in the future."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            if domain.upper() == "PROVIDER" and field_name == "npi" and (
                not normalized_value
                or not re.fullmatch(r"\d{10}", normalized_value)
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="NPI_CHECKSUM_VALIDITY",
                    dimension="VALIDITY", severity="CRITICAL", outcome="SKIP"
                )

            # ------------------------------------------
            # Provider - NPI checksum
            # ------------------------------------------

            if (
                domain.upper() == "PROVIDER"
                and field_name == "npi"
                and normalized_value
                and re.fullmatch(
                    r"\d{10}",
                    normalized_value,
                )
            ):
                passed = self._is_valid_npi(
                    normalized_value
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="NPI_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="CRITICAL",
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id="NPI_CHECKSUM_VALIDITY",
                            dimension="VALIDITY",
                            severity="CRITICAL",
                            message=(
                                "npi failed the CMS NPI "
                                "check-digit validation."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            if field_name == "phone_number" and (
                not normalized_value
                or self._is_junk_phone(normalized_value)
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="PHONE_NUMBER_SEMANTIC_VALIDITY",
                    dimension="VALIDITY", severity=field_config.severity, outcome="SKIP"
                )

            # ------------------------------------------
            # Phone semantic validity
            # ------------------------------------------

            if (
                field_name == "phone_number"
                and normalized_value
                and not self._is_junk_phone(
                    normalized_value
                )
):
                passed = (
                    self._is_valid_phone_number(
                        normalized_value
                    )
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="PHONE_NUMBER_SEMANTIC_VALIDITY",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                "PHONE_NUMBER"
                                "_SEMANTIC_VALIDITY"
                            ),
                            dimension="VALIDITY",
                            severity=(
                                field_config.severity
                            ),
                            message=(
                                "phone_number is structurally "
                                "formatted but does not appear "
                                "to be a plausible NANP phone number."
                            ),
                            observed_value=(
                                normalized_value
                            ),
                        )
                    )

            if domain.upper() == "PRODUCT" and field_name == "gtin" and (
                not normalized_value
                or not re.fullmatch(r"(?:\d{8}|\d{12}|\d{13}|\d{14})", normalized_value)
            ):
                self._observe_rule_execution(
                    rule_execution_state, rule_id="GTIN_CHECKSUM_VALIDITY",
                    dimension="VALIDITY", severity="HIGH", outcome="SKIP"
                )

            # ------------------------------------------
            # Product - GTIN checksum
            # ------------------------------------------

            if (
                domain.upper() == "PRODUCT"
                and field_name == "gtin"
                and normalized_value
                and re.fullmatch(
                    r"(?:\d{8}|\d{12}|\d{13}|\d{14})",
                    normalized_value,
                )
            ):
                passed = self._is_valid_gtin(
                    normalized_value
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id="GTIN_CHECKSUM_VALIDITY",
                    dimension="VALIDITY",
                    severity="HIGH",
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id="GTIN_CHECKSUM_VALIDITY",
                            dimension="VALIDITY",
                            severity="HIGH",
                            message=(
                                "gtin failed the GTIN "
                                "check-digit validation."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            # ------------------------------------------
            # Product Variant - governed UOM vocabulary
            # ------------------------------------------

            if (
                domain.upper() == "PRODUCT"
                and field_name == "product_variant"
                and field_config.allowed_uom_values
            ):
                variant_match = re.fullmatch(
                    r"([0-9]+(?:\.[0-9]+)?) ([A-Z]+)",
                    normalized_value,
                ) if normalized_value else None

                if not variant_match:
                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id="PRODUCT_VARIANT_UOM_VALIDITY",
                        dimension="VALIDITY",
                        severity=field_config.severity,
                        outcome="SKIP",
                    )
                else:
                    uom_value = variant_match.group(2).upper()
                    allowed_uoms = {
                        str(item).strip().upper()
                        for item in field_config.allowed_uom_values
                        if str(item).strip()
                    }
                    passed = uom_value in allowed_uoms

                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id="PRODUCT_VARIANT_UOM_VALIDITY",
                        dimension="VALIDITY",
                        severity=field_config.severity,
                        outcome="PASS" if passed else "FAIL",
                    )

                    dimension_results["VALIDITY"].append(
                        (100.0 if passed else 0.0, weight)
                    )

                    if not passed:
                        findings.append(
                            self._finding(
                                organization_id=organization_id,
                                profile_run_id=profile_run_id,
                                domain=domain,
                                source_table=source_table,
                                source_row_id=source_row_id,
                                record_id=record_id,
                                field_name=field_name,
                                rule_id="PRODUCT_VARIANT_UOM_VALIDITY",
                                dimension="VALIDITY",
                                severity=field_config.severity,
                                message=(
                                    "product_variant uses a UOM outside "
                                    "the approved governed vocabulary."
                                ),
                                observed_value=normalized_value,
                            )
                        )

            # ------------------------------------------
            # Product Variant - authoritative governance validity
            # ------------------------------------------
            #
            # Policy-facing rule:
            #   PRODUCT_VARIANT_VALIDITY
            #
            # PASS only when a populated Product Variant contains:
            #   1) a numeric quantity,
            #   2) exactly one separator space,
            #   3) a UOM token,
            #   4) a UOM contained in the approved governed vocabulary.
            #
            # Examples:
            #   "3 OZ"   -> PASS when OZ is approved
            #   "3"      -> FAIL (missing UOM)
            #   "4  OZ"  -> FAIL (invalid spacing)
            #   "3 oz"   -> FAIL (not canonical uppercase format)
            #   "3 XX"   -> FAIL when XX is not approved
            #
            # Blank/null optional values are SKIP rather than FAIL.
            # This rule is evidence-only and intentionally does not add
            # another finding or another record-score penalty; the
            # diagnostic rules already explain the underlying defect.
            if (
                domain.upper() == "PRODUCT"
                and field_name == "product_variant"
                and field_config.allowed_uom_values
            ):
                if not normalized_value:
                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id="PRODUCT_VARIANT_VALIDITY",
                        dimension="VALIDITY",
                        severity=field_config.severity,
                        outcome="SKIP",
                    )
                else:
                    governance_match = re.fullmatch(
                        r"([0-9]+(?:\.[0-9]+)?) ([A-Z]+)",
                        normalized_value,
                    )

                    allowed_uoms = {
                        str(item).strip().upper()
                        for item in field_config.allowed_uom_values
                        if str(item).strip()
                    }

                    governance_passed = bool(
                        governance_match
                        and governance_match.group(2).upper()
                        in allowed_uoms
                    )

                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id="PRODUCT_VARIANT_VALIDITY",
                        dimension="VALIDITY",
                        severity=field_config.severity,
                        outcome=(
                            "PASS"
                            if governance_passed
                            else "FAIL"
                        ),
                    )

            # ------------------------------------------
            # Validity - allowed values
            # ------------------------------------------

            if not normalized_value and field_config.allowed_values:
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_ALLOWED_VALUE",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="SKIP",
                )

            if (
                normalized_value
                and field_config.allowed_values
            ):
                allowed = {
                    str(item).strip().upper()
                    for item in field_config.allowed_values
                }

                passed = normalized_value.upper() in allowed

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_ALLOWED_VALUE",
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "VALIDITY"
                ].append(
                    (
                        100.0 if passed else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                f"{field_name.upper()}"
                                "_ALLOWED_VALUE"
                            ),
                            dimension="VALIDITY",
                            severity=field_config.severity,
                            message=(
                                f"{field_name} contains a value "
                                "outside the approved domain."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            # ------------------------------------------
            # Validity - junk / placeholder values
            # ------------------------------------------

            # Every configured field receives explicit junk-value
            # execution evidence:
            #
            #   blank / null value -> SKIP
            #   populated value    -> PASS or FAIL
            #
            # This keeps rule execution accounting complete for
            # optional fields such as GTIN and product_variant.
            #
            # A blank optional field is not a junk-value failure;
            # it is simply not applicable to this rule.

            junk_rule_id = f"{field_name.upper()}_JUNK_VALUE"

            if not normalized_value:
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=junk_rule_id,
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="SKIP",
                )

            else:
                is_junk = False

                if field_name == "phone_number":
                    is_junk = self._is_junk_phone(
                        normalized_value
                    )

                elif field_name in {
                    "full_name",
                    "first_name",
                    "last_name",
                    "supplier_name",
                    "product_name",
                    "institution_name",
                    "specialty",
                    "address",
                }:
                    is_junk = self._is_junk_text(
                        normalized_value
                    )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=junk_rule_id,
                    dimension="VALIDITY",
                    severity=field_config.severity,
                    outcome="FAIL" if is_junk else "PASS",
                )

                if is_junk:
                    dimension_results[
                        "VALIDITY"
                    ].append(
                        (
                            0.0,
                            weight,
                        )
                    )

                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=junk_rule_id,
                            dimension="VALIDITY",
                            severity=field_config.severity,
                            message=(
                                f"{field_name} appears to contain "
                                "a placeholder or low-quality value."
                            ),
                            observed_value=normalized_value,
                        )
                    )

            # ------------------------------------------
            # Standardization
            # ------------------------------------------

            if (
                not normalized_value
                and field_config.standardization_required
            ):
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_STANDARDIZATION",
                    dimension="STANDARDIZATION",
                    severity="LOW",
                    outcome="SKIP",
                )

            if (
                normalized_value
                and field_config.standardization_required
            ):
                observed_standardization_value = str(value)

                standardized = self._canonical_string(
                    observed_standardization_value
                )

                passed = (
                    observed_standardization_value
                    == standardized
                )

                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=f"{field_name.upper()}_STANDARDIZATION",
                    dimension="STANDARDIZATION",
                    severity="LOW",
                    outcome="PASS" if passed else "FAIL",
                )

                dimension_results[
                    "STANDARDIZATION"
                ].append(
                    (
                        100.0
                        if passed
                        else 0.0,
                        weight,
                    )
                )

                if not passed:
                    findings.append(
                        self._finding(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            domain=domain,
                            source_table=source_table,
                            source_row_id=source_row_id,
                            record_id=record_id,
                            field_name=field_name,
                            rule_id=(
                                f"{field_name.upper()}"
                                "_STANDARDIZATION"
                            ),
                            dimension=(
                                "STANDARDIZATION"
                            ),
                            severity="LOW",
                            message=(
                                f"{field_name} "
                                "is not in canonical "
                                "format."
                            ),
                            observed_value=(
                                observed_standardization_value
                            ),
                            proposed_value=(
                                standardized
                            ),
                        )
                    )

            # ------------------------------------------
            # Uniqueness
            # ------------------------------------------

            if (
                field_config.uniqueness_key
                and field_name == config.business_key_field
            ):
                uniqueness_value = self._normalize_identifier(
                    value
                )

                if not uniqueness_value:
                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id=f"{field_name.upper()}_UNIQUENESS",
                        dimension="UNIQUENESS",
                        severity="HIGH",
                        outcome="SKIP",
                    )

                if uniqueness_value:
                    passed = (
                        uniqueness_value
                        not in duplicate_keys
                    )

                    self._observe_rule_execution(
                        rule_execution_state,
                        rule_id=f"{field_name.upper()}_UNIQUENESS",
                        dimension="UNIQUENESS",
                        severity="HIGH",
                        outcome="PASS" if passed else "FAIL",
                    )

                    dimension_results[
                        "UNIQUENESS"
                    ].append(
                        (
                            100.0
                            if passed
                            else 0.0,
                            weight,
                        )
                    )

                    if not passed:
                        findings.append(
                            self._finding(
                                organization_id=organization_id,
                                profile_run_id=profile_run_id,
                                domain=domain,
                                source_table=source_table,
                                source_row_id=source_row_id,
                                record_id=record_id,
                                field_name=field_name,
                                rule_id=(
                                    f"{field_name.upper()}"
                                    "_UNIQUENESS"
                                ),
                                dimension="UNIQUENESS",
                                severity="HIGH",
                                message=(
                                    f"{field_name} "
                                    "is duplicated across "
                                    "multiple records."
                                ),
                                observed_value=(
                                    uniqueness_value
                    ),
                )
            )

        # ----------------------------------------------
        # Composite identity uniqueness
        # ----------------------------------------------

        for rule in config.composite_uniqueness_rules:
            key = tuple(
                self._normalize_identifier(
                    row.get(field_name)
                )
                for field_name in rule.fields
            )

            # Optional identity fields are evaluated only when the entire
            # composite key is populated. Completeness is handled separately.
            if not key or any(not value for value in key):
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=rule.rule_id,
                    dimension="UNIQUENESS",
                    severity=rule.severity,
                    outcome="SKIP",
                )
                continue

            passed = key not in composite_duplicate_keys.get(
                rule.rule_id,
                set(),
            )

            self._observe_rule_execution(
                rule_execution_state,
                rule_id=rule.rule_id,
                dimension="UNIQUENESS",
                severity=rule.severity,
                outcome="PASS" if passed else "FAIL",
            )

            dimension_results[
                "UNIQUENESS"
            ].append(
                (
                    100.0 if passed else 0.0,
                    float(rule.weight),
                )
            )

            if not passed:
                field_label = "+".join(rule.fields)
                observed_value = " | ".join(key)

                findings.append(
                    self._finding(
                        organization_id=organization_id,
                        profile_run_id=profile_run_id,
                        domain=domain,
                        source_table=source_table,
                        source_row_id=source_row_id,
                        record_id=record_id,
                        field_name=field_label,
                        rule_id=rule.rule_id,
                        dimension="UNIQUENESS",
                        severity=rule.severity,
                        message=(
                            f"Composite identity {field_label} "
                            "is duplicated across multiple records."
                        ),
                        observed_value=observed_value,
                    )
                )

        # ----------------------------------------------
        # Identifier mapping collisions
        # ----------------------------------------------

        for rule in config.mapping_collision_rules:
            key_value = self._normalize_identifier(
                row.get(rule.key_field)
            )
            mapped_value = self._normalize_identifier(
                row.get(rule.mapped_field)
            )

            if not key_value or not mapped_value:
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=rule.rule_id,
                    dimension="CONSISTENCY",
                    severity=rule.severity,
                    outcome="SKIP",
                )
                continue

            passed = key_value not in mapping_collision_keys.get(
                rule.rule_id,
                set(),
            )

            self._observe_rule_execution(
                rule_execution_state,
                rule_id=rule.rule_id,
                dimension="CONSISTENCY",
                severity=rule.severity,
                outcome="PASS" if passed else "FAIL",
            )

            dimension_results[
                "CONSISTENCY"
            ].append(
                (
                    100.0 if passed else 0.0,
                    float(rule.weight),
                )
            )

            if not passed:
                findings.append(
                    self._finding(
                        organization_id=organization_id,
                        profile_run_id=profile_run_id,
                        domain=domain,
                        source_table=source_table,
                        source_row_id=source_row_id,
                        record_id=record_id,
                        field_name=rule.key_field,
                        rule_id=rule.rule_id,
                        dimension="CONSISTENCY",
                        severity=rule.severity,
                        message=(
                            f"{rule.key_field} maps to multiple "
                            f"{rule.mapped_field} values across "
                            "the profiled dataset."
                        ),
                        observed_value=key_value,
                    )
                )

        # ----------------------------------------------
        # Cross-field consistency
        # ----------------------------------------------

        for rule in config.cross_field_rules:
            fields = self._parse_composite_unique_fields(
                rule.sql_condition
            )

            # Unsupported declarative conditions are intentionally
            # NOT scored. They remain NOT_IMPLEMENTED in the
            # rule-execution evidence initialized for this profile.
            if not fields:
                continue

            key = tuple(
                self._normalize_identifier(
                    row.get(field_name)
                )
                for field_name in fields
            )

            if not key or any(not value for value in key):
                self._observe_rule_execution(
                    rule_execution_state,
                    rule_id=rule.rule_id,
                    dimension="CONSISTENCY",
                    severity=rule.severity,
                    outcome="SKIP",
                )
                continue

            passed = key not in (
                cross_field_composite_duplicate_keys.get(
                    rule.rule_id,
                    set(),
                )
            )

            self._observe_rule_execution(
                rule_execution_state,
                rule_id=rule.rule_id,
                dimension="CONSISTENCY",
                severity=rule.severity,
                outcome="PASS" if passed else "FAIL",
            )

            dimension_results["CONSISTENCY"].append(
                (100.0 if passed else 0.0, 1.0)
            )

            if not passed:
                field_label = "+".join(fields)
                findings.append(
                    self._finding(
                        organization_id=organization_id,
                        profile_run_id=profile_run_id,
                        domain=domain,
                        source_table=source_table,
                        source_row_id=source_row_id,
                        record_id=record_id,
                        field_name=field_label,
                        rule_id=rule.rule_id,
                        dimension="CONSISTENCY",
                        severity=rule.severity,
                        message=(
                            f"Composite identity {field_label} is "
                            "duplicated across multiple records."
                        ),
                        observed_value=" | ".join(key),
                    )
                )

        dimension_scores = {
            dimension: self._weighted_score(
                values
            )
            for dimension, values
            in dimension_results.items()
        }

        overall_score = (
            self._overall_score(
                dimension_scores
            )
        )

        severity_counts = (
            self._severity_counts(
                findings
            )
        )

        return (
            findings,
            RecordQualityScore(
                organization_id=(
                    organization_id
                ),
                profile_run_id=(
                    profile_run_id
                ),
                domain=domain,
                source_table=(
                    source_table
                ),
                source_row_id=source_row_id,
                record_id=record_id,
                completeness_score=(
                    dimension_scores[
                        "COMPLETENESS"
                    ]
                ),
                validity_score=(
                    dimension_scores[
                        "VALIDITY"
                    ]
                ),
                uniqueness_score=(
                    dimension_scores[
                        "UNIQUENESS"
                    ]
                ),
                standardization_score=(
                    dimension_scores[
                        "STANDARDIZATION"
                    ]
                ),
                consistency_score=(
                    dimension_scores[
                        "CONSISTENCY"
                    ]
                ),
                overall_score=(
                    overall_score
                ),
                issue_count=len(
                    findings
                ),
                critical_issue_count=(
                    severity_counts[
                        "CRITICAL"
                    ]
                ),
                high_issue_count=(
                    severity_counts[
                        "HIGH"
                    ]
                ),
                medium_issue_count=(
                    severity_counts[
                        "MEDIUM"
                    ]
                ),
                low_issue_count=(
                    severity_counts[
                        "LOW"
                    ]
                ),
            ),
        )

    # ---------------------------------------------------------
    # Cross-field consistency
    # ---------------------------------------------------------

    def _evaluate_cross_field_rules(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_table: str,
        record_id: str,
        row: Dict[str, Any],
        rules: Sequence[CrossFieldRule],
    ) -> List[QualityFinding]:
        """
        Phase 1 supports simple Python-style field comparisons
        through known rule IDs.

        SQL-backed cross-field evaluation can be added later.
        """

        findings: List[
            QualityFinding
        ] = []

        for rule in rules:
            if not rule.rule_id:
                continue

            # For now this service keeps cross-field
            # conditions configuration-ready rather
            # than evaluating arbitrary user SQL
            # inside Python.
            #
            # SQL cross-field profiling should be
            # performed in a dedicated safe query
            # execution method in Phase 2.
        
        
        return findings

    # ---------------------------------------------------------
    # Result aggregation
    # ---------------------------------------------------------
    
    
    def _build_result(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_table: str,
        minimum_record_score: float,
        findings: List[
            QualityFinding
        ],
        scores: List[
            RecordQualityScore
        ],
        rule_executions: List[
            QualityRuleExecution
        ],
        total_records: int,
        duplicate_record_count: int,
    ) -> QualityProfileResult:

        severity_counts = (
            self._severity_counts(
                findings
            )
        )

        records_with_findings = len({
            finding.source_row_id
            for finding in findings
        })

        records_below_threshold = sum(
            1
            for score
            in scores
            if score.overall_score
            < minimum_record_score
        )

        return QualityProfileResult(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            domain=domain,
            source_table=source_table,
            total_records=total_records,
            scored_record_count=len(
                scores
            ),
            avg_record_score=(
                self._average(
                    score.overall_score
                    for score
                    in scores
                )
            ),
            avg_completeness_score=(
                self._average(
                    score.completeness_score
                    for score
                    in scores
                )
            ),
            avg_validity_score=(
                self._average(
                    score.validity_score
                    for score
                    in scores
                )
            ),
            avg_uniqueness_score=(
                self._average(
                    score.uniqueness_score
                    for score
                    in scores
                )
            ),
            avg_standardization_score=(
                self._average(
                    score.standardization_score
                    for score
                    in scores
                )
            ),
            avg_consistency_score=(
                self._average(
                    score.consistency_score
                    for score
                    in scores
                )
            ),
            duplicate_record_count=(
                duplicate_record_count
            ),
            records_below_threshold=(
                records_below_threshold
            ),
            records_with_findings=(
                records_with_findings
            ),
            total_findings=len(
                findings
            ),
            critical_findings=(
                severity_counts[
                    "CRITICAL"
                ]
            ),
            high_findings=(
                severity_counts[
                    "HIGH"
                ]
            ),
            medium_findings=(
                severity_counts[
                    "MEDIUM"
                ]
            ),
            low_findings=(
                severity_counts[
                    "LOW"
                ]
            ),
            findings=findings,
            record_scores=scores,
            rule_executions=rule_executions,
            generated_at=datetime.now(
                timezone.utc
            ),
        )

    @staticmethod
    def _count_duplicate_records(
        *,
        rows: Sequence[Dict[str, Any]],
        business_key_field: str,
        duplicate_keys: set[str],
    ) -> int:
        """
        Number of records participating in duplicate business-key groups.
        """

        if not duplicate_keys:
            return 0

        count = 0

        for row in rows:
            key = QualityProfilerService._normalize_identifier(
                row.get(business_key_field)
            )

            if key and key in duplicate_keys:
                count += 1

        return count

    # ---------------------------------------------------------
    # Scoring helpers
    # ---------------------------------------------------------

    @staticmethod
    def _weighted_score(
        values: Sequence[
            tuple[float, float]
        ],
    ) -> float:

        if not values:
            # No rule configured for this
            # dimension = neutral / healthy.
            return 100.0

        numerator = sum(
            score * weight
            for score, weight
            in values
        )

        denominator = sum(
            weight
            for _, weight
            in values
        )

        if denominator <= 0:
            return 100.0

        return round(
            numerator / denominator,
            2,
        )

    @staticmethod
    def _overall_score(
        dimension_scores: Dict[
            str,
            float,
        ],
    ) -> float:

        weights = {
            "COMPLETENESS": 0.25,
            "VALIDITY": 0.25,
            "UNIQUENESS": 0.20,
            "STANDARDIZATION": 0.15,
            "CONSISTENCY": 0.15,
        }

        total = sum(
            dimension_scores.get(
                dimension,
                100.0,
            )
            * weight
            for dimension, weight
            in weights.items()
        )

        return round(
            max(
                0.0,
                min(
                    100.0,
                    total,
                ),
            ),
            2,
        )

    @staticmethod
    def _average(
        values: Sequence[float] | Any,
    ) -> float:

        values_list = list(
            values
        )

        if not values_list:
            return 100.0

        return round(
            sum(values_list)
            / len(values_list),
            2,
        )

    @staticmethod
    def _severity_counts(
        findings: Sequence[
            QualityFinding
        ],
    ) -> Dict[str, int]:

        counts = {
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
        }

        for finding in findings:
            severity = str(
                finding.severity
                or "MEDIUM"
            ).upper()

            if severity not in counts:
                severity = "MEDIUM"

            counts[severity] += 1

        return counts

    # ---------------------------------------------------------
    # Normalization
    # ---------------------------------------------------------

    @staticmethod
    def _normalize_value(
        value: Any,
    ) -> str:

        if value is None:
            return ""

        return str(
            value
        ).strip()

    @staticmethod
    def _canonical_string(
        value: str,
    ) -> str:

        normalized = " ".join(
            str(value)
            .strip()
            .split()
        )

        return normalized.upper()

    # ---------------------------------------------------------
    # Finding factory
    # ---------------------------------------------------------

    @staticmethod
    def _finding(
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        source_table: str,
        source_row_id: str,
        record_id: str,
        field_name: Optional[str],
        rule_id: str,
        dimension: str,
        severity: str,
        message: str,
        observed_value: Optional[str],
        proposed_value: Optional[str] = None,
    ) -> QualityFinding:

        safe_dimension = str(
            dimension
        ).upper()

        if (
            safe_dimension
            not in VALID_DIMENSIONS
        ):
            raise ValueError(
                f"Unsupported DQ dimension: "
                f"{safe_dimension}"
            )

        safe_severity = str(
            severity
        ).upper()

        if (
            safe_severity
            not in VALID_SEVERITIES
        ):
            safe_severity = "MEDIUM"

        fingerprint_source = (
            f"{organization_id}|"
            f"{profile_run_id}|"
            f"{source_row_id}|"
            f"{record_id}|"
            f"{rule_id}|"
            f"{field_name or ''}"
)

        fingerprint = (
            hashlib.sha256(
                fingerprint_source.encode(
                    "utf-8"
                )
            )
            .hexdigest()[:20]
        )

        return QualityFinding(
            finding_id=f"dqf_{fingerprint}",
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            domain=domain,
            source_table=source_table,
            source_row_id=source_row_id,
            record_id=record_id,
            field_name=field_name,
            rule_id=rule_id,
            dimension=safe_dimension,
            severity=safe_severity,
            finding_message=message,
            observed_value=observed_value,
            proposed_value=proposed_value,
        )