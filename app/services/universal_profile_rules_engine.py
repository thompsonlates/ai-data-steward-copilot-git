from __future__ import annotations

"""
Universal deterministic profile-rule engine for AI Data Steward Copilot.

Flow:
source rows -> SemanticTypeEngine -> ProfileRuleRegistry
-> UniversalProfileRulesEngine -> deterministic findings / executions
-> QualityProfilerService -> Quality Intelligence -> Governed AI suggestions.

Only ACTIVE registry rules execute. This module is connector-neutral, LLM-free,
repository-neutral, and does not activate governance or execute remediation.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import math
import re
from typing import Any, Mapping, Sequence

from app.services.profile_rule_registry import (
    ProfileRuleRegistry,
    ResolvedProfileRule,
    RuleEvaluator,
    RuleResolutionContext,
)
from app.services.semantic_type_engine import (
    SemanticTypeEngine,
    SemanticTypeInference,
)


@dataclass(frozen=True)
class UniversalRuleFinding:
    organization_id: str
    profile_run_id: str
    domain: str
    source_name: str
    rule_id: str
    rule_version: str
    logical_key: str
    rule_family: str
    evaluator: str
    severity: str
    field_name: str | None
    semantic_type: str | None
    source_row_id: str
    record_id: str
    finding_message: str
    observed_value: str | None = None
    proposed_value: str | None = None
    deterministic: bool = True
    remediation_available: bool = False
    remediation_type: str | None = None


@dataclass(frozen=True)
class UniversalRuleExecution:
    organization_id: str
    profile_run_id: str
    domain: str
    source_name: str
    rule_id: str
    rule_version: str
    logical_key: str
    rule_family: str
    evaluator: str
    severity: str
    field_name: str | None
    semantic_type: str | None
    evaluated_record_count: int
    passed_record_count: int
    failed_record_count: int
    skipped_record_count: int
    execution_status: str
    compliance_rate: float | None
    deterministic: bool = True


@dataclass(frozen=True)
class UniversalProfileRuleResult:
    organization_id: str
    profile_run_id: str
    domain: str
    source_name: str
    total_records: int
    resolved_rule_count: int
    executed_rule_count: int
    total_findings: int
    semantic_inferences: Mapping[str, SemanticTypeInference]
    resolved_rules: tuple[ResolvedProfileRule, ...]
    findings: tuple[UniversalRuleFinding, ...]
    rule_executions: tuple[UniversalRuleExecution, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class _Eval:
    evaluated: bool
    passed: bool
    message: str = ""
    observed: str | None = None
    proposed: str | None = None


class UniversalProfileRulesEngine:
    """Resolve and execute ACTIVE deterministic profile rules."""

    DEFAULT_JUNK_VALUES = frozenset({
        "", "na", "n/a", "none", "null", "unknown", "test", "testing",
        "sample", "dummy", "xxx", "xxxx", "asdf", "qwerty",
        "placeholder", "abcdefj", "123r23r23",
    })

    def __init__(
        self,
        *,
        rule_registry: ProfileRuleRegistry | None = None,
        semantic_type_engine: SemanticTypeEngine | None = None,
    ) -> None:
        self.rule_registry = rule_registry or ProfileRuleRegistry()
        self.semantic_type_engine = semantic_type_engine or SemanticTypeEngine()

    def evaluate_profile(
        self,
        *,
        organization_id: str,
        domain: str,
        profile_run_id: str,
        rows: Sequence[Mapping[str, Any]],
        source_name: str,
        business_key_field: str,
        mapped_fields: Sequence[str] | None = None,
        canonical_to_physical: Mapping[str, str] | None = None,
        sample_values_by_field: Mapping[str, Sequence[Any]] | None = None,
        declared_types_by_field: Mapping[str, Any] | None = None,
        governed_semantic_overrides: Mapping[str, str] | None = None,
        as_of: datetime | None = None,
    ) -> UniversalProfileRuleResult:
        org = self._require_org(organization_id)
        dom = self._require(domain, "domain").upper()
        run_id = self._require(profile_run_id, "profile_run_id")
        source = self._require(source_name, "source_name")
        key_field = self.semantic_type_engine.normalize_field_name(business_key_field)
        if not key_field:
            raise ValueError("business_key_field is required.")

        normalized_rows = tuple(self._normalize_row(r) for r in rows)
        fields = self._mapped_fields(normalized_rows, mapped_fields)

        samples = (
            dict(sample_values_by_field)
            if sample_values_by_field is not None
            else self._samples(normalized_rows, fields)
        )

        inferences = self.semantic_type_engine.infer_mapped_fields(
            mapped_fields=fields,
            canonical_to_physical=canonical_to_physical,
            sample_values_by_field=samples,
            declared_types_by_field=declared_types_by_field,
            governed_overrides=governed_semantic_overrides,
        )

        semantic_types = self.semantic_type_engine.semantic_types_for_registry(
            inferences
        )

        resolved = self.rule_registry.resolve_active_rules(
            context=RuleResolutionContext(
                organization_id=org,
                domain=dom,
                mapped_fields=fields,
                semantic_types_by_field=semantic_types,
                as_of=as_of,
            )
        )

        findings: list[UniversalRuleFinding] = []
        executions: list[UniversalRuleExecution] = []

        for rule in resolved:
            found, execution = self._execute_rule(
                org=org,
                domain=dom,
                run_id=run_id,
                source=source,
                key_field=key_field,
                rows=normalized_rows,
                rule=rule,
                as_of=as_of,
            )
            findings.extend(found)
            executions.append(execution)

        return UniversalProfileRuleResult(
            organization_id=org,
            profile_run_id=run_id,
            domain=dom,
            source_name=source,
            total_records=len(normalized_rows),
            resolved_rule_count=len(resolved),
            executed_rule_count=len(executions),
            total_findings=len(findings),
            semantic_inferences=inferences,
            resolved_rules=resolved,
            findings=tuple(findings),
            rule_executions=tuple(executions),
        )

    @staticmethod
    def _require_org(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("organization_id is required.")
        if not value.startswith("org_"):
            raise ValueError("organization_id must use the org_ identifier standard.")
        return value

    @staticmethod
    def _require(value: Any, name: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{name} is required.")
        return value

    def _normalize_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise TypeError("Each profile row must be a mapping.")
        result: dict[str, Any] = {}
        for key, value in row.items():
            name = self.semantic_type_engine.normalize_field_name(key)
            if name:
                result[name] = value
        return result

    def _mapped_fields(
        self,
        rows: Sequence[Mapping[str, Any]],
        mapped_fields: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if mapped_fields is None:
            names = {name for row in rows for name in row}
        else:
            names = {
                self.semantic_type_engine.normalize_field_name(name)
                for name in mapped_fields
            }
        return tuple(sorted(name for name in names if name))

    @staticmethod
    def _samples(
        rows: Sequence[Mapping[str, Any]],
        fields: Sequence[str],
        limit: int = 100,
    ) -> dict[str, tuple[Any, ...]]:
        result: dict[str, list[Any]] = {f: [] for f in fields}
        for row in rows:
            for name in fields:
                if len(result[name]) >= limit:
                    continue
                value = row.get(name)
                if value is not None and (not isinstance(value, str) or value.strip()):
                    result[name].append(value)
        return {k: tuple(v) for k, v in result.items()}

    def _execute_rule(
        self,
        *,
        org: str,
        domain: str,
        run_id: str,
        source: str,
        key_field: str,
        rows: Sequence[Mapping[str, Any]],
        rule: ResolvedProfileRule,
        as_of: datetime | None,
    ) -> tuple[list[UniversalRuleFinding], UniversalRuleExecution]:
        evaluator = rule.definition.evaluator

        if evaluator == RuleEvaluator.UNIQUE:
            return self._unique(org, domain, run_id, source, key_field, rows, rule)

        if evaluator == RuleEvaluator.COMPOSITE_UNIQUE:
            return self._composite_unique(
                org, domain, run_id, source, key_field, rows, rule
            )

        if evaluator == RuleEvaluator.CROSS_FIELD:
            return self._cross_field(
                org, domain, run_id, source, key_field, rows, rule
            )

        if evaluator == RuleEvaluator.REFERENCE_INTEGRITY:
            return self._reference_integrity(
                org, domain, run_id, source, key_field, rows, rule
            )

        if evaluator == RuleEvaluator.ROW_COUNT_ANOMALY:
            return self._row_count(org, domain, run_id, source, rows, rule)

        if evaluator == RuleEvaluator.VALUE_DISTRIBUTION:
            return self._distribution(org, domain, run_id, source, rows, rule)

        if evaluator == RuleEvaluator.SCHEMA_CONFORMITY:
            return self._schema(org, domain, run_id, source, rows, rule)

        return self._row_rule(
            org, domain, run_id, source, key_field, rows, rule, as_of
        )

    def _row_rule(
        self, org, domain, run_id, source, key_field, rows, rule, as_of
    ):
        if not rule.field_name:
            return [], self._execution(
                org, domain, run_id, source, rule, 0, 0, 0, len(rows), "SKIPPED"
            )

        findings = []
        evaluated = passed = failed = skipped = 0

        for index, row in enumerate(rows):
            result = self._eval_value(rule, row.get(rule.field_name), as_of)

            if not result.evaluated:
                skipped += 1
                continue

            evaluated += 1
            if result.passed:
                passed += 1
                continue

            failed += 1
            row_id, record_id = self._identity(row, index, key_field)
            findings.append(self._finding(
                org, domain, run_id, source, rule, row_id, record_id,
                result.message, result.observed, result.proposed
            ))

        return findings, self._execution(
            org, domain, run_id, source, rule,
            evaluated, passed, failed, skipped, "SUCCEEDED"
        )

    def _eval_value(
        self,
        rule: ResolvedProfileRule,
        value: Any,
        as_of: datetime | None,
    ) -> _Eval:
        evaluator = rule.definition.evaluator
        p = dict(rule.definition.parameters)

        if evaluator == RuleEvaluator.REQUIRED_VALUE:
            missing = self._missing(value)
            return _Eval(
                True, not missing,
                "Required value is missing." if missing else "",
                None if value is None else str(value),
            )

        # Requiredness is separate governance. Other evaluators skip nulls.
        if self._missing(value):
            return _Eval(False, True)

        text = str(value)

        if evaluator == RuleEvaluator.REGEX:
            ok = bool(re.fullmatch(str(p["pattern"]), text))
            return _Eval(
                True, ok,
                "Value does not satisfy the governed format." if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.ALLOWED_VALUES:
            case_sensitive = bool(p.get("case_sensitive", False))
            normalize = (lambda x: str(x).strip()) if case_sensitive else (
                lambda x: str(x).strip().upper()
            )
            allowed = {normalize(v) for v in p["allowed_values"]}
            ok = normalize(value) in allowed
            return _Eval(
                True, ok,
                "Value is outside the ACTIVE governed allowed-value set."
                if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.JUNK_VALUE:
            junk = {
                str(v).strip().lower()
                for v in p.get("junk_values", self.DEFAULT_JUNK_VALUES)
            }
            ok = text.strip().lower() not in junk
            return _Eval(
                True, ok,
                "Value matches a governed placeholder/junk-value token."
                if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.WHITESPACE:
            proposed = re.sub(r"\s+", " ", text.strip())
            ok = text == proposed
            return _Eval(
                True, ok,
                "Value requires deterministic whitespace normalization."
                if not ok else "",
                text,
                None if ok else proposed,
            )

        if evaluator == RuleEvaluator.CASE:
            mode = str(p.get("required_case", "UPPER")).upper()
            transforms = {
                "UPPER": str.upper,
                "LOWER": str.lower,
                "TITLE": str.title,
            }
            if mode not in transforms:
                raise ValueError(f"Unsupported CASE mode={mode}.")
            proposed = transforms[mode](text)
            ok = text == proposed
            return _Eval(
                True, ok,
                f"Value does not satisfy governed {mode} case standard."
                if not ok else "",
                text,
                None if ok else proposed,
            )

        if evaluator == RuleEvaluator.LENGTH:
            length = len(text)
            minimum = p.get("min_length")
            maximum = p.get("max_length")
            ok = (
                (minimum is None or length >= int(minimum))
                and (maximum is None or length <= int(maximum))
            )
            return _Eval(
                True, ok,
                "Value length is outside the ACTIVE governed range."
                if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.CHECKSUM:
            algorithm = str(p.get("algorithm", "")).upper()
            validators = {
                "NPI": self._valid_npi,
                "GTIN": self._valid_gtin,
                "ABA": self._valid_aba,
                "ABA_ROUTING": self._valid_aba,
                "ABA_ROUTING_NUMBER": self._valid_aba,
            }
            if algorithm not in validators:
                raise ValueError(f"Unsupported checksum algorithm={algorithm}.")
            ok = validators[algorithm](text)
            return _Eval(
                True, ok,
                f"Value fails governed {algorithm} checksum validation."
                if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.DATE_VALIDITY:
            formats = p.get("formats") or ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d")
            ok = False
            for fmt in formats:
                try:
                    datetime.strptime(text.strip(), str(fmt))
                    ok = True
                    break
                except ValueError:
                    pass
            return _Eval(
                True, ok,
                "Value is not a valid governed date." if not ok else "",
                text,
            )

        if evaluator == RuleEvaluator.FRESHNESS:
            max_days = p.get("max_age_days")
            if max_days is None:
                raise ValueError("FRESHNESS requires max_age_days.")
            parsed = self._datetime(value)
            if parsed is None:
                return _Eval(
                    True, False,
                    "Value cannot be interpreted as a date/time for freshness evaluation.",
                    text,
                )
            point = as_of or datetime.now(timezone.utc)
            ok = (point - parsed).total_seconds() / 86400 <= float(max_days)
            return _Eval(
                True, ok,
                f"Value exceeds governed freshness threshold of {max_days} day(s)."
                if not ok else "",
                text,
            )

        raise ValueError(f"Unsupported evaluator={evaluator.value}.")

    def _unique(self, org, domain, run_id, source, key_field, rows, rule):
        field_name = rule.field_name
        if not field_name:
            raise ValueError(f"{rule.rule_id}: UNIQUE requires a field.")

        values = [self._comparable(r.get(field_name)) for r in rows]
        counts = Counter(v for v in values if v is not None)
        findings = []
        evaluated = passed = failed = skipped = 0

        for index, (row, value) in enumerate(zip(rows, values)):
            if value is None:
                skipped += 1
                continue
            evaluated += 1
            if counts[value] == 1:
                passed += 1
                continue
            failed += 1
            row_id, record_id = self._identity(row, index, key_field)
            findings.append(self._finding(
                org, domain, run_id, source, rule, row_id, record_id,
                "Populated value is not unique within the profiled dataset.",
                str(row.get(field_name)),
            ))

        return findings, self._execution(
            org, domain, run_id, source, rule,
            evaluated, passed, failed, skipped, "SUCCEEDED"
        )

    def _composite_unique(self, org, domain, run_id, source, key_field, rows, rule):
        fields = tuple(
            self.semantic_type_engine.normalize_field_name(v)
            for v in rule.definition.parameters.get("fields", ())
            if self.semantic_type_engine.normalize_field_name(v)
        )
        if len(fields) < 2:
            raise ValueError(
                f"{rule.rule_id}: COMPOSITE_UNIQUE requires at least two fields."
            )

        keys = []
        counts = Counter()
        for row in rows:
            parts = tuple(self._comparable(row.get(f)) for f in fields)
            key = None if any(v is None for v in parts) else tuple(parts)
            keys.append(key)
            if key is not None:
                counts[key] += 1

        findings = []
        evaluated = passed = failed = skipped = 0
        for index, (row, key) in enumerate(zip(rows, keys)):
            if key is None:
                skipped += 1
                continue
            evaluated += 1
            if counts[key] == 1:
                passed += 1
                continue
            failed += 1
            row_id, record_id = self._identity(row, index, key_field)
            findings.append(self._finding(
                org, domain, run_id, source, rule, row_id, record_id,
                "Composite governed key is duplicated within the profiled dataset.",
                " | ".join(key),
            ))

        return findings, self._execution(
            org, domain, run_id, source, rule,
            evaluated, passed, failed, skipped, "SUCCEEDED"
        )

    def _cross_field(self, org, domain, run_id, source, key_field, rows, rule):
        p = dict(rule.definition.parameters)
        left = self.semantic_type_engine.normalize_field_name(p.get("left_field"))
        right = self.semantic_type_engine.normalize_field_name(p.get("right_field"))
        operator = str(p.get("operator", "")).upper()

        if not left or not right:
            raise ValueError(f"{rule.rule_id}: CROSS_FIELD requires both fields.")
        if operator not in {"EQUAL", "NOT_EQUAL", "BOTH_PRESENT", "AT_LEAST_ONE_PRESENT"}:
            raise ValueError(f"{rule.rule_id}: unsupported CROSS_FIELD operator.")

        findings = []
        passed = failed = 0

        for index, row in enumerate(rows):
            lv, rv = row.get(left), row.get(right)
            lm, rm = self._missing(lv), self._missing(rv)

            if operator == "EQUAL":
                ok = not lm and not rm and self._comparable(lv) == self._comparable(rv)
            elif operator == "NOT_EQUAL":
                ok = not lm and not rm and self._comparable(lv) != self._comparable(rv)
            elif operator == "BOTH_PRESENT":
                ok = not lm and not rm
            else:
                ok = not (lm and rm)

            if ok:
                passed += 1
                continue

            failed += 1
            row_id, record_id = self._identity(row, index, key_field)
            findings.append(self._finding(
                org, domain, run_id, source, rule, row_id, record_id,
                f"Cross-field governed condition {operator} failed.",
                f"{left}={lv!r}; {right}={rv!r}",
            ))

        return findings, self._execution(
            org, domain, run_id, source, rule,
            len(rows), passed, failed, 0, "SUCCEEDED"
        )

    def _reference_integrity(self, org, domain, run_id, source, key_field, rows, rule):
        p = dict(rule.definition.parameters)
        field_name = rule.field_name or self.semantic_type_engine.normalize_field_name(
            p.get("field_name")
        )
        refs = p.get("reference_values")
        if not field_name or not isinstance(refs, (list, tuple, set)):
            raise ValueError(
                f"{rule.rule_id}: REFERENCE_INTEGRITY requires field/reference_values."
            )

        allowed = {self._comparable(v) for v in refs if self._comparable(v) is not None}
        findings = []
        evaluated = passed = failed = skipped = 0

        for index, row in enumerate(rows):
            raw = row.get(field_name)
            value = self._comparable(raw)
            if value is None:
                skipped += 1
                continue
            evaluated += 1
            if value in allowed:
                passed += 1
                continue
            failed += 1
            row_id, record_id = self._identity(row, index, key_field)
            findings.append(self._finding(
                org, domain, run_id, source, rule, row_id, record_id,
                "Value does not resolve to the governed reference set.",
                str(raw),
            ))

        return findings, self._execution(
            org, domain, run_id, source, rule,
            evaluated, passed, failed, skipped, "SUCCEEDED"
        )

    def _row_count(self, org, domain, run_id, source, rows, rule):
        p = dict(rule.definition.parameters)
        minimum, maximum = p.get("min_count"), p.get("max_count")
        if minimum is None and maximum is None:
            raise ValueError(f"{rule.rule_id}: ROW_COUNT_ANOMALY needs a threshold.")
        count = len(rows)
        ok = (
            (minimum is None or count >= int(minimum))
            and (maximum is None or count <= int(maximum))
        )
        findings = [] if ok else [self._finding(
            org, domain, run_id, source, rule, "__DATASET__", "__DATASET__",
            "Dataset row count is outside the ACTIVE governed range.", str(count)
        )]
        return findings, self._execution(
            org, domain, run_id, source, rule, 1,
            1 if ok else 0, 0 if ok else 1, 0, "SUCCEEDED"
        )

    def _distribution(self, org, domain, run_id, source, rows, rule):
        p = dict(rule.definition.parameters)
        field_name = rule.field_name or self.semantic_type_engine.normalize_field_name(
            p.get("field_name")
        )
        threshold = p.get("max_single_value_ratio")
        if not field_name or threshold is None:
            raise ValueError(
                f"{rule.rule_id}: VALUE_DISTRIBUTION requires field/threshold."
            )

        values = [self._comparable(r.get(field_name)) for r in rows]
        populated = [v for v in values if v is not None]
        if not populated:
            return [], self._execution(
                org, domain, run_id, source, rule, 0, 0, 0, len(rows), "SKIPPED"
            )

        value, count = Counter(populated).most_common(1)[0]
        ratio = count / len(populated)
        ok = ratio <= float(threshold)
        findings = [] if ok else [self._finding(
            org, domain, run_id, source, rule, "__DATASET__", "__DATASET__",
            "Observed value distribution exceeds the ACTIVE governed concentration threshold.",
            f"value={value!r}; ratio={ratio:.6f}",
        )]
        return findings, self._execution(
            org, domain, run_id, source, rule, len(populated),
            len(populated) if ok else 0,
            0 if ok else len(populated),
            len(rows) - len(populated), "SUCCEEDED"
        )

    def _schema(self, org, domain, run_id, source, rows, rule):
        required = {
            self.semantic_type_engine.normalize_field_name(v)
            for v in rule.definition.parameters.get("required_fields", ())
            if self.semantic_type_engine.normalize_field_name(v)
        }
        if not required:
            raise ValueError(f"{rule.rule_id}: SCHEMA_CONFORMITY needs required_fields.")

        observed = {name for row in rows for name in row}
        missing = sorted(required - observed)
        ok = not missing
        findings = [] if ok else [self._finding(
            org, domain, run_id, source, rule, "__DATASET__", "__DATASET__",
            "Dataset is missing ACTIVE governed required field(s).",
            ", ".join(missing),
        )]
        return findings, self._execution(
            org, domain, run_id, source, rule, 1,
            1 if ok else 0, 0 if ok else 1, 0, "SUCCEEDED"
        )

    def _finding(
        self, org, domain, run_id, source, rule,
        row_id, record_id, message, observed=None, proposed=None
    ):
        d = rule.definition
        return UniversalRuleFinding(
            organization_id=org,
            profile_run_id=run_id,
            domain=domain,
            source_name=source,
            rule_id=d.rule_id,
            rule_version=d.version,
            logical_key=d.logical_key,
            rule_family=d.family.value,
            evaluator=d.evaluator.value,
            severity=d.severity,
            field_name=rule.field_name,
            semantic_type=rule.semantic_type,
            source_row_id=row_id,
            record_id=record_id,
            finding_message=message,
            observed_value=observed,
            proposed_value=proposed,
            deterministic=d.deterministic,
            remediation_available=d.remediation_available,
            remediation_type=d.remediation_type,
        )

    def _execution(
        self, org, domain, run_id, source, rule,
        evaluated, passed, failed, skipped, status
    ):
        d = rule.definition
        return UniversalRuleExecution(
            organization_id=org,
            profile_run_id=run_id,
            domain=domain,
            source_name=source,
            rule_id=d.rule_id,
            rule_version=d.version,
            logical_key=d.logical_key,
            rule_family=d.family.value,
            evaluator=d.evaluator.value,
            severity=d.severity,
            field_name=rule.field_name,
            semantic_type=rule.semantic_type,
            evaluated_record_count=evaluated,
            passed_record_count=passed,
            failed_record_count=failed,
            skipped_record_count=skipped,
            execution_status=status,
            compliance_rate=round(passed / evaluated, 6) if evaluated else None,
            deterministic=d.deterministic,
        )

    @staticmethod
    def _missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, float) and math.isnan(value):
            return True
        return isinstance(value, str) and not value.strip()

    @classmethod
    def _comparable(cls, value: Any) -> str | None:
        if cls._missing(value):
            return None
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip().casefold()

    @staticmethod
    def _identifier(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _identity(self, row, index, key_field):
        value = self._identifier(row.get(key_field))
        return (value, value) if value else (f"row_{index + 1}", f"row_{index + 1}")

    @staticmethod
    def _valid_npi(value: str) -> bool:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 10 or len(set(digits)) == 1:
            return False
        total = 0
        for i, char in enumerate(("80840" + digits[:9])[::-1]):
            number = int(char)
            if i % 2 == 0:
                number *= 2
                if number > 9:
                    number -= 9
            total += number
        return (10 - total % 10) % 10 == int(digits[-1])

    @staticmethod
    def _valid_gtin(value: str) -> bool:
        digits = re.sub(r"\D", "", value)
        if len(digits) not in {8, 12, 13, 14} or len(set(digits)) == 1:
            return False
        total = sum(
            int(char) * (3 if i % 2 == 0 else 1)
            for i, char in enumerate(reversed(digits[:-1]))
        )
        return (10 - total % 10) % 10 == int(digits[-1])

    @staticmethod
    def _valid_aba(value: str) -> bool:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 9 or len(set(digits)) == 1:
            return False
        weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
        return sum(int(d) * w for d, w in zip(digits, weights)) % 10 == 0

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

        text = str(value or "").strip()
        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
        except ValueError:
            pass

        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return None


__all__ = [
    "UniversalProfileRuleResult",
    "UniversalProfileRulesEngine",
    "UniversalRuleExecution",
    "UniversalRuleFinding",
]
