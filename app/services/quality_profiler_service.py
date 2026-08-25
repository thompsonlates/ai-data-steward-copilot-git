from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from google.cloud import bigquery


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

@dataclass(frozen=True)
class QualityFieldConfig:
    field_name: str

    required: bool = False

    uniqueness_key: bool = False

    regex_pattern: Optional[str] = None

    allowed_values: Sequence[str] = field(
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
class QualityProfileConfig:
    domain: str

    source_table: str

    business_key_field: str

    fields: Sequence[QualityFieldConfig]

    cross_field_rules: Sequence[CrossFieldRule] = field(
        default_factory=tuple
    )

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


    generated_at: datetime




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
    ) -> None:
        self.client = client or bigquery.Client(
            project=project_id
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

        duplicate_keys = (
            self._find_duplicate_business_keys(
                rows=normalized_rows,
                business_key_field=business_key_field,
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
            )

            findings.extend(
                row_findings
            )

            scores.append(
                row_score
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
            total_records=len(
                normalized_rows
            ),
            duplicate_record_count=(
                self._count_duplicate_records(
                    rows=normalized_rows,
                    business_key_field=(
                        business_key_field
                    ),
                    duplicate_keys=(
                        duplicate_keys
                    ),
                )
            ),
        )

        return result
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
                normalized_value
                and field_config.regex_pattern
            ):
                passed = bool(
                    re.fullmatch(
                        field_config.regex_pattern,
                        normalized_value,
                    )
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
            # Validity - allowed values
            # ------------------------------------------

            if (
                normalized_value
                and field_config.allowed_values
            ):
                allowed = {
                    str(item).strip().upper()
                    for item in field_config.allowed_values
                }

                passed = normalized_value.upper() in allowed

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

            if normalized_value:
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
                            rule_id=(
                                f"{field_name.upper()}"
                                "_JUNK_VALUE"
                            ),
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
                normalized_value
                and
                field_config
                .standardization_required
            ):
                standardized = (
                    self._canonical_string(
                        normalized_value
                    )
                )

                passed = (
                    normalized_value
                    == standardized
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
                                normalized_value
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

                if uniqueness_value:
                    passed = (
                        uniqueness_value
                        not in duplicate_keys
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
        # Cross-field consistency
        # ----------------------------------------------

        consistency_findings = (
            self._evaluate_cross_field_rules(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                domain=domain,
                source_table=source_table,
                record_id=record_id,
                row=row,
                rules=config.cross_field_rules,
            )
        )

        findings.extend(
            consistency_findings
        )

        if config.cross_field_rules:
            failed_rule_ids = {
                item.rule_id
                for item
                in consistency_findings
            }

            for rule in config.cross_field_rules:
                dimension_results[
                    "CONSISTENCY"
                ].append(
                    (
                        0.0
                        if rule.rule_id
                        in failed_rule_ids
                        else 100.0,
                        1.0,
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
    )