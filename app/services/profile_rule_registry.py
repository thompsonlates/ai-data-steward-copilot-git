from __future__ import annotations

"""
Governed profile-rule registry for AI Data Steward Copilot.

Purpose
-------
This module defines the canonical contract for deterministic profile rules and
resolves which rules are eligible for a profile run.

It intentionally DOES NOT:
- read source data
- execute rules
- call an LLM
- persist BigQuery rows
- infer semantic types
- evaluate Governance Policy compliance

Those responsibilities remain in the profiler / universal rules engine,
semantic type engine, repositories, AI services, and Policy Intelligence.

Resolution precedence
---------------------
UNIVERSAL < SEMANTIC_TYPE < DOMAIN < ORGANIZATION

Only ACTIVE rules are returned for deterministic execution.
More-specific rules may replace less-specific rules only when they share the
same logical_key. This prevents accidental duplicate enforcement while keeping
rule IDs/version history immutable and explainable.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Canonical enums
# ---------------------------------------------------------------------------

class RuleScope(str, Enum):
    UNIVERSAL = "UNIVERSAL"
    SEMANTIC_TYPE = "SEMANTIC_TYPE"
    DOMAIN = "DOMAIN"
    ORGANIZATION = "ORGANIZATION"


class RuleStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class RuleFamily(str, Enum):
    COMPLETENESS = "COMPLETENESS"
    VALIDITY = "VALIDITY"
    STANDARDIZATION = "STANDARDIZATION"
    UNIQUENESS = "UNIQUENESS"
    CONSISTENCY = "CONSISTENCY"
    REFERENTIAL = "REFERENTIAL"
    FRESHNESS = "FRESHNESS"
    DISTRIBUTION = "DISTRIBUTION"
    STRUCTURAL = "STRUCTURAL"


class RuleEvaluator(str, Enum):
    """
    Declarative evaluator names understood by UniversalProfileRulesEngine.

    Keep this list deterministic. Do not put arbitrary Python or SQL in a
    governed rule definition.
    """
    REQUIRED_VALUE = "REQUIRED_VALUE"
    REGEX = "REGEX"
    ALLOWED_VALUES = "ALLOWED_VALUES"
    UNIQUE = "UNIQUE"
    COMPOSITE_UNIQUE = "COMPOSITE_UNIQUE"
    JUNK_VALUE = "JUNK_VALUE"
    WHITESPACE = "WHITESPACE"
    CASE = "CASE"
    LENGTH = "LENGTH"
    CHECKSUM = "CHECKSUM"
    DATE_VALIDITY = "DATE_VALIDITY"
    CROSS_FIELD = "CROSS_FIELD"
    REFERENCE_INTEGRITY = "REFERENCE_INTEGRITY"
    FRESHNESS = "FRESHNESS"
    ROW_COUNT_ANOMALY = "ROW_COUNT_ANOMALY"
    VALUE_DISTRIBUTION = "VALUE_DISTRIBUTION"
    SCHEMA_CONFORMITY = "SCHEMA_CONFORMITY"


VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_SCOPE_PRECEDENCE = {
    RuleScope.UNIVERSAL: 10,
    RuleScope.SEMANTIC_TYPE: 20,
    RuleScope.DOMAIN: 30,
    RuleScope.ORGANIZATION: 40,
}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_rule_id(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]+", "_", _upper(value)).strip("_")
    if not normalized:
        raise ValueError("rule_id is required.")
    return normalized


def _normalize_logical_key(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_:.\-]+", "_", _upper(value)).strip("_")
    if not normalized:
        raise ValueError("logical_key is required.")
    return normalized


def _normalize_version(value: str) -> str:
    normalized = _text(value)
    if not normalized:
        raise ValueError("version is required.")
    if len(normalized) > 64:
        raise ValueError("version must be 64 characters or fewer.")
    return normalized


def _normalize_severity(value: str) -> str:
    normalized = _upper(value or "MEDIUM")
    if normalized not in VALID_SEVERITIES:
        raise ValueError(
            f"Unsupported severity={value!r}. "
            f"Expected one of {sorted(VALID_SEVERITIES)}."
        )
    return normalized


def _normalize_string_tuple(values: Sequence[str] | None, *, upper: bool = False) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values or ():
        item = _upper(value) if upper else _lower(value)
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)

    return tuple(normalized)


def _freeze_parameters(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Defensive shallow copy.

    Rule parameters remain JSON-compatible dictionaries because they will
    eventually be persisted to DQ_RULE_CONFIG / governed rule storage.
    """
    return dict(value or {})


# ---------------------------------------------------------------------------
# Canonical governed rule contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProfileRuleDefinition:
    rule_id: str
    version: str
    logical_key: str

    scope: RuleScope
    family: RuleFamily
    evaluator: RuleEvaluator

    status: RuleStatus = RuleStatus.PROPOSED
    severity: str = "MEDIUM"
    description: str = ""

    # Scope selectors -------------------------------------------------------
    # UNIVERSAL: normally none of these are required.
    # SEMANTIC_TYPE: semantic_types should be populated.
    # DOMAIN: domains should be populated.
    # ORGANIZATION: organization_id is required; domain/field selectors may
    #               further narrow the tenant-specific standard.
    organization_id: str | None = None
    domains: tuple[str, ...] = field(default_factory=tuple)
    semantic_types: tuple[str, ...] = field(default_factory=tuple)
    field_names: tuple[str, ...] = field(default_factory=tuple)

    # Deterministic execution contract -------------------------------------
    parameters: Mapping[str, Any] = field(default_factory=dict)
    deterministic: bool = True
    remediation_available: bool = False
    remediation_type: str | None = None

    # Governance / provenance ----------------------------------------------
    origin: str = "ADMS_DEFAULT"
    created_by: str = "system"
    approved_by: str | None = None
    activated_by: str | None = None

    created_at: datetime = field(default_factory=_utc_now)
    approved_at: datetime | None = None
    activated_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _normalize_rule_id(self.rule_id))
        object.__setattr__(self, "version", _normalize_version(self.version))
        object.__setattr__(self, "logical_key", _normalize_logical_key(self.logical_key))
        object.__setattr__(self, "severity", _normalize_severity(self.severity))
        object.__setattr__(self, "description", _text(self.description))
        object.__setattr__(self, "organization_id", _text(self.organization_id) or None)
        object.__setattr__(self, "domains", _normalize_string_tuple(self.domains, upper=True))
        object.__setattr__(
            self,
            "semantic_types",
            _normalize_string_tuple(self.semantic_types, upper=True),
        )
        object.__setattr__(
            self,
            "field_names",
            _normalize_string_tuple(self.field_names, upper=False),
        )
        object.__setattr__(self, "parameters", _freeze_parameters(self.parameters))
        object.__setattr__(self, "origin", _upper(self.origin or "ADMS_DEFAULT"))
        object.__setattr__(self, "created_by", _text(self.created_by or "system"))
        object.__setattr__(self, "approved_by", _text(self.approved_by) or None)
        object.__setattr__(self, "activated_by", _text(self.activated_by) or None)
        object.__setattr__(
            self,
            "remediation_type",
            _upper(self.remediation_type) or None,
        )

        self._validate_scope()
        self._validate_lifecycle()
        self._validate_execution_contract()

    @property
    def registry_key(self) -> tuple[str, str]:
        """Immutable identity of one governed rule version."""
        return self.rule_id, self.version

    @property
    def precedence(self) -> int:
        return _SCOPE_PRECEDENCE[self.scope]

    def _validate_scope(self) -> None:
        if self.scope == RuleScope.SEMANTIC_TYPE and not self.semantic_types:
            raise ValueError(
                f"{self.rule_id}: SEMANTIC_TYPE scope requires semantic_types."
            )

        if self.scope == RuleScope.DOMAIN and not self.domains:
            raise ValueError(
                f"{self.rule_id}: DOMAIN scope requires domains."
            )

        if self.scope == RuleScope.ORGANIZATION:
            if not self.organization_id:
                raise ValueError(
                    f"{self.rule_id}: ORGANIZATION scope requires organization_id."
                )
            if not self.organization_id.startswith("org_"):
                raise ValueError(
                    f"{self.rule_id}: organization_id must use the org_ tenant identifier."
                )

    def _validate_lifecycle(self) -> None:
        if self.status == RuleStatus.ACTIVE:
            if not self.deterministic:
                raise ValueError(
                    f"{self.rule_id}: ACTIVE profile rules must be deterministic."
                )
            if not self.activated_by:
                raise ValueError(
                    f"{self.rule_id}: ACTIVE rules require activated_by."
                )
            if self.activated_at is None:
                raise ValueError(
                    f"{self.rule_id}: ACTIVE rules require activated_at."
                )

        if self.status in {
            RuleStatus.APPROVED,
            RuleStatus.ACTIVE,
            RuleStatus.DISABLED,
            RuleStatus.RETIRED,
        }:
            if not self.approved_by:
                raise ValueError(
                    f"{self.rule_id}: {self.status.value} rules require approved_by."
                )
            if self.approved_at is None:
                raise ValueError(
                    f"{self.rule_id}: {self.status.value} rules require approved_at."
                )

        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError(
                f"{self.rule_id}: effective_to must be after effective_from."
            )

    def _validate_execution_contract(self) -> None:
        if not self.deterministic and self.status == RuleStatus.ACTIVE:
            raise ValueError(
                f"{self.rule_id}: non-deterministic rules cannot become ACTIVE."
            )

        if self.remediation_available and not self.remediation_type:
            raise ValueError(
                f"{self.rule_id}: remediation_available requires remediation_type."
            )

        if self.evaluator == RuleEvaluator.REGEX:
            pattern = _text(self.parameters.get("pattern"))
            if not pattern:
                raise ValueError(
                    f"{self.rule_id}: REGEX evaluator requires parameters.pattern."
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"{self.rule_id}: invalid regex pattern: {exc}"
                ) from exc

        if self.evaluator == RuleEvaluator.ALLOWED_VALUES:
            values = self.parameters.get("allowed_values")
            if not isinstance(values, (list, tuple, set)) or not values:
                raise ValueError(
                    f"{self.rule_id}: ALLOWED_VALUES requires non-empty "
                    "parameters.allowed_values."
                )

        if self.evaluator == RuleEvaluator.LENGTH:
            if (
                self.parameters.get("min_length") is None
                and self.parameters.get("max_length") is None
            ):
                raise ValueError(
                    f"{self.rule_id}: LENGTH requires min_length and/or max_length."
                )

    def is_effective(self, at: datetime | None = None) -> bool:
        if self.status != RuleStatus.ACTIVE:
            return False

        point = at or _utc_now()

        if self.effective_from is not None and point < self.effective_from:
            return False

        if self.effective_to is not None and point >= self.effective_to:
            return False

        return True

    def matches_context(
        self,
        *,
        organization_id: str,
        domain: str,
        field_name: str | None = None,
        semantic_type: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        """Return True only when this ACTIVE rule applies to the supplied context."""
        if not self.is_effective(at):
            return False

        effective_org = _text(organization_id)
        effective_domain = _upper(domain)
        effective_field = _lower(field_name)
        effective_semantic = _upper(semantic_type)

        if self.scope == RuleScope.ORGANIZATION:
            if self.organization_id != effective_org:
                return False

        if self.domains and effective_domain not in self.domains:
            return False

        if self.field_names and effective_field not in self.field_names:
            return False

        if self.semantic_types and effective_semantic not in self.semantic_types:
            return False

        return True

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "logical_key": self.logical_key,
            "scope": self.scope.value,
            "family": self.family.value,
            "evaluator": self.evaluator.value,
            "status": self.status.value,
            "severity": self.severity,
            "description": self.description,
            "organization_id": self.organization_id,
            "domains": list(self.domains),
            "semantic_types": list(self.semantic_types),
            "field_names": list(self.field_names),
            "parameters": dict(self.parameters),
            "deterministic": self.deterministic,
            "remediation_available": self.remediation_available,
            "remediation_type": self.remediation_type,
            "origin": self.origin,
            "created_by": self.created_by,
            "approved_by": self.approved_by,
            "activated_by": self.activated_by,
            "created_at": iso(self.created_at),
            "approved_at": iso(self.approved_at),
            "activated_at": iso(self.activated_at),
            "effective_from": iso(self.effective_from),
            "effective_to": iso(self.effective_to),
        }


@dataclass(frozen=True)
class RuleResolutionContext:
    organization_id: str
    domain: str
    mapped_fields: tuple[str, ...] = field(default_factory=tuple)
    semantic_types_by_field: Mapping[str, str] = field(default_factory=dict)
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        organization_id = _text(self.organization_id)
        if not organization_id:
            raise ValueError("organization_id is required.")
        if not organization_id.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ tenant identifier standard."
            )

        domain = _upper(self.domain)
        if not domain:
            raise ValueError("domain is required.")

        mapped_fields = _normalize_string_tuple(self.mapped_fields, upper=False)
        semantic_types = {
            _lower(field_name): _upper(semantic_type)
            for field_name, semantic_type in dict(
                self.semantic_types_by_field or {}
            ).items()
            if _lower(field_name) and _upper(semantic_type)
        }

        object.__setattr__(self, "organization_id", organization_id)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "mapped_fields", mapped_fields)
        object.__setattr__(self, "semantic_types_by_field", semantic_types)


@dataclass(frozen=True)
class ResolvedProfileRule:
    """
    A rule bound to a concrete source field for one profile run.

    field_name=None is reserved for dataset-level rules such as row-count,
    schema, distribution, or cross-field checks.
    """
    definition: ProfileRuleDefinition
    field_name: str | None = None
    semantic_type: str | None = None

    @property
    def rule_id(self) -> str:
        return self.definition.rule_id

    @property
    def version(self) -> str:
        return self.definition.version

    @property
    def logical_key(self) -> str:
        return self.definition.logical_key


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ProfileRuleRegistry:
    """
    In-memory governed rule registry.

    This is deliberately repository-neutral. Later, BigQuery DQ_RULE_CONFIG
    rows can be loaded into this class without changing the Universal Rules
    Engine contract.
    """

    def __init__(
        self,
        rules: Iterable[ProfileRuleDefinition] | None = None,
        *,
        include_adms_defaults: bool = True,
    ) -> None:
        self._rules: dict[tuple[str, str], ProfileRuleDefinition] = {}

        if include_adms_defaults:
            self.register_many(build_adms_default_rules())

        if rules:
            self.register_many(rules)

    def register(self, rule: ProfileRuleDefinition) -> None:
        if not isinstance(rule, ProfileRuleDefinition):
            raise TypeError("rule must be a ProfileRuleDefinition.")

        key = rule.registry_key
        if key in self._rules:
            raise ValueError(
                f"Rule version already registered: {rule.rule_id} {rule.version}."
            )

        self._rules[key] = rule

    def register_many(
        self,
        rules: Iterable[ProfileRuleDefinition],
    ) -> None:
        for rule in rules:
            self.register(rule)

    def get(
        self,
        *,
        rule_id: str,
        version: str,
    ) -> ProfileRuleDefinition | None:
        return self._rules.get(
            (_normalize_rule_id(rule_id), _normalize_version(version))
        )

    def all_rules(self) -> tuple[ProfileRuleDefinition, ...]:
        return tuple(
            sorted(
                self._rules.values(),
                key=lambda item: (
                    item.rule_id,
                    item.version,
                ),
            )
        )

    def active_rules(
        self,
        *,
        organization_id: str,
        domain: str,
        at: datetime | None = None,
    ) -> tuple[ProfileRuleDefinition, ...]:
        effective_org = _text(organization_id)
        effective_domain = _upper(domain)

        return tuple(
            rule
            for rule in self._rules.values()
            if rule.matches_context(
                organization_id=effective_org,
                domain=effective_domain,
                at=at,
            )
        )

    def resolve_active_rules(
        self,
        *,
        context: RuleResolutionContext,
    ) -> tuple[ResolvedProfileRule, ...]:
        """
        Resolve deterministic ACTIVE rules for this profile run.

        Resolution behavior:
        - dataset-level rules are returned once
        - field-level rules are bound only to mapped fields
        - semantic rules use semantic_types_by_field
        - more-specific scope replaces a less-specific rule only when both
          share the same logical_key for the same field
        """
        candidates: list[ResolvedProfileRule] = []

        for definition in self._rules.values():
            if not definition.is_effective(context.as_of):
                continue

            # Tenant/domain gates that do not depend on a specific field.
            if definition.scope == RuleScope.ORGANIZATION:
                if definition.organization_id != context.organization_id:
                    continue

            if definition.domains and context.domain not in definition.domains:
                continue

            is_field_rule = bool(
                definition.field_names
                or definition.semantic_types
                or definition.evaluator in {
                    RuleEvaluator.REQUIRED_VALUE,
                    RuleEvaluator.REGEX,
                    RuleEvaluator.ALLOWED_VALUES,
                    RuleEvaluator.UNIQUE,
                    RuleEvaluator.JUNK_VALUE,
                    RuleEvaluator.WHITESPACE,
                    RuleEvaluator.CASE,
                    RuleEvaluator.LENGTH,
                    RuleEvaluator.CHECKSUM,
                    RuleEvaluator.DATE_VALIDITY,
                }
            )

            if not is_field_rule:
                candidates.append(
                    ResolvedProfileRule(
                        definition=definition,
                        field_name=None,
                        semantic_type=None,
                    )
                )
                continue

            for field_name in context.mapped_fields:
                semantic_type = context.semantic_types_by_field.get(field_name)

                if not definition.matches_context(
                    organization_id=context.organization_id,
                    domain=context.domain,
                    field_name=field_name,
                    semantic_type=semantic_type,
                    at=context.as_of,
                ):
                    continue

                candidates.append(
                    ResolvedProfileRule(
                        definition=definition,
                        field_name=field_name,
                        semantic_type=semantic_type,
                    )
                )

        # Resolve logical-key collisions by specificity.
        selected: dict[tuple[str | None, str], ResolvedProfileRule] = {}

        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.definition.precedence,
                item.definition.rule_id,
                item.definition.version,
            ),
        ):
            key = (
                candidate.field_name,
                candidate.definition.logical_key,
            )

            current = selected.get(key)
            if current is None:
                selected[key] = candidate
                continue

            if (
                candidate.definition.precedence
                > current.definition.precedence
            ):
                selected[key] = candidate
                continue

            if (
                candidate.definition.precedence
                == current.definition.precedence
            ):
                raise ValueError(
                    "Ambiguous ACTIVE profile rules for "
                    f"field={candidate.field_name!r}, "
                    f"logical_key={candidate.definition.logical_key!r}: "
                    f"{current.rule_id}/{current.version} and "
                    f"{candidate.rule_id}/{candidate.version}."
                )

        return tuple(
            sorted(
                selected.values(),
                key=lambda item: (
                    item.field_name or "",
                    item.definition.family.value,
                    item.definition.rule_id,
                ),
            )
        )

    def proposed_rules(
        self,
        *,
        organization_id: str | None = None,
    ) -> tuple[ProfileRuleDefinition, ...]:
        effective_org = _text(organization_id)

        return tuple(
            rule
            for rule in self._rules.values()
            if rule.status == RuleStatus.PROPOSED
            and (
                not effective_org
                or rule.organization_id in {None, effective_org}
            )
        )

    def transition(
        self,
        *,
        rule_id: str,
        version: str,
        new_status: RuleStatus,
        actor: str,
        at: datetime | None = None,
    ) -> ProfileRuleDefinition:
        """
        In-memory lifecycle helper.

        Persistence/audit will belong to the governed rule repository. This
        method exists so the lifecycle contract can be tested now.
        """
        key = (_normalize_rule_id(rule_id), _normalize_version(version))
        current = self._rules.get(key)

        if current is None:
            raise KeyError(f"Unknown rule version: {key[0]} {key[1]}.")

        actor_name = _text(actor)
        if not actor_name:
            raise ValueError("actor is required.")

        point = at or _utc_now()

        allowed: dict[RuleStatus, set[RuleStatus]] = {
            RuleStatus.PROPOSED: {
                RuleStatus.APPROVED,
                RuleStatus.REJECTED,
            },
            RuleStatus.APPROVED: {
                RuleStatus.ACTIVE,
                RuleStatus.REJECTED,
            },
            RuleStatus.ACTIVE: {
                RuleStatus.DISABLED,
                RuleStatus.RETIRED,
            },
            RuleStatus.DISABLED: {
                RuleStatus.ACTIVE,
                RuleStatus.RETIRED,
            },
            RuleStatus.RETIRED: set(),
            RuleStatus.REJECTED: set(),
        }

        if new_status not in allowed[current.status]:
            raise ValueError(
                f"Invalid rule lifecycle transition: "
                f"{current.status.value} -> {new_status.value}."
            )

        updates: dict[str, Any] = {"status": new_status}

        if new_status == RuleStatus.APPROVED:
            updates.update(
                approved_by=actor_name,
                approved_at=point,
            )

        if new_status == RuleStatus.ACTIVE:
            updates.update(
                approved_by=current.approved_by or actor_name,
                approved_at=current.approved_at or point,
                activated_by=actor_name,
                activated_at=point,
            )

        if new_status in {RuleStatus.DISABLED, RuleStatus.RETIRED}:
            # Preserve prior approval/activation provenance.
            updates.update(
                approved_by=current.approved_by,
                approved_at=current.approved_at,
            )

        updated = replace(current, **updates)
        self._rules[key] = updated
        return updated


# ---------------------------------------------------------------------------
# ADMS default governed rules
# ---------------------------------------------------------------------------

def _active_default(
    *,
    rule_id: str,
    logical_key: str,
    scope: RuleScope,
    family: RuleFamily,
    evaluator: RuleEvaluator,
    description: str,
    severity: str = "MEDIUM",
    domains: Sequence[str] = (),
    semantic_types: Sequence[str] = (),
    field_names: Sequence[str] = (),
    parameters: Mapping[str, Any] | None = None,
    remediation_available: bool = False,
    remediation_type: str | None = None,
) -> ProfileRuleDefinition:
    """
    Construct an ADMS-shipped ACTIVE deterministic rule.

    Product/domain-specific legacy rules are intentionally NOT duplicated here
    yet. They stay in QualityProfilerService / quality_profile_config_factory
    until migrated under regression tests.
    """
    now = _utc_now()

    return ProfileRuleDefinition(
        rule_id=rule_id,
        version="1",
        logical_key=logical_key,
        scope=scope,
        family=family,
        evaluator=evaluator,
        status=RuleStatus.ACTIVE,
        severity=severity,
        description=description,
        domains=tuple(domains),
        semantic_types=tuple(semantic_types),
        field_names=tuple(field_names),
        parameters=dict(parameters or {}),
        deterministic=True,
        remediation_available=remediation_available,
        remediation_type=remediation_type,
        origin="ADMS_DEFAULT",
        created_by="adms",
        approved_by="adms",
        activated_by="adms",
        created_at=now,
        approved_at=now,
        activated_at=now,
        effective_from=now,
    )


def build_adms_default_rules() -> tuple[ProfileRuleDefinition, ...]:
    """
    Small, conservative initial registry.

    These defaults establish the contract without changing existing profiler
    behavior. The UniversalProfileRulesEngine can consume them next.

    Important:
    - Requiredness is NOT universal; it is governed metadata and will be
      supplied by domain/organization rules.
    - Identifier character policy is NOT guessed universally.
    - Location/Organization business standards are NOT invented here.
    """
    return (
        _active_default(
            rule_id="UNIVERSAL_JUNK_VALUE",
            logical_key="VALUE:JUNK_VALUE",
            scope=RuleScope.UNIVERSAL,
            family=RuleFamily.VALIDITY,
            evaluator=RuleEvaluator.JUNK_VALUE,
            description=(
                "Detect known placeholder or low-information values using "
                "the deterministic ADMS junk-value vocabulary."
            ),
            severity="MEDIUM",
        ),
        _active_default(
            rule_id="UNIVERSAL_WHITESPACE_STANDARDIZATION",
            logical_key="VALUE:WHITESPACE_STANDARDIZATION",
            scope=RuleScope.UNIVERSAL,
            family=RuleFamily.STANDARDIZATION,
            evaluator=RuleEvaluator.WHITESPACE,
            description=(
                "Detect leading, trailing, or repeated internal whitespace "
                "without changing semantic content."
            ),
            severity="LOW",
            remediation_available=True,
            remediation_type="NORMALIZE_WHITESPACE",
        ),
        _active_default(
            rule_id="EMAIL_FORMAT_VALIDITY",
            logical_key="SEMANTIC:EMAIL:FORMAT_VALIDITY",
            scope=RuleScope.SEMANTIC_TYPE,
            family=RuleFamily.VALIDITY,
            evaluator=RuleEvaluator.REGEX,
            semantic_types=("EMAIL",),
            description="Validate the structural format of populated email values.",
            severity="MEDIUM",
            parameters={
                "pattern": (
                    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
                    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
                )
            },
        ),
        _active_default(
            rule_id="PHONE_FORMAT_VALIDITY",
            logical_key="SEMANTIC:PHONE:FORMAT_VALIDITY",
            scope=RuleScope.SEMANTIC_TYPE,
            family=RuleFamily.VALIDITY,
            evaluator=RuleEvaluator.REGEX,
            semantic_types=("PHONE",),
            description="Validate the structural format of populated U.S. phone values.",
            severity="MEDIUM",
            parameters={
                "pattern": (
                    r"^(?:\+?1[\s.\-]?)?"
                    r"(?:\(?\d{3}\)?[\s.\-]?)"
                    r"\d{3}[\s.\-]?\d{4}$"
                )
            },
        ),
        _active_default(
            rule_id="DATE_FORMAT_VALIDITY",
            logical_key="SEMANTIC:DATE:FORMAT_VALIDITY",
            scope=RuleScope.SEMANTIC_TYPE,
            family=RuleFamily.VALIDITY,
            evaluator=RuleEvaluator.REGEX,
            semantic_types=("DATE",),
            description="Validate populated values against supported date structures.",
            severity="MEDIUM",
            parameters={
                "pattern": (
                    r"^(?:\d{4}-\d{2}-\d{2}|"
                    r"\d{2}/\d{2}/\d{4}|"
                    r"\d{4}/\d{2}/\d{2})$"
                )
            },
        ),
    )


__all__ = [
    "ProfileRuleDefinition",
    "ProfileRuleRegistry",
    "ResolvedProfileRule",
    "RuleEvaluator",
    "RuleFamily",
    "RuleResolutionContext",
    "RuleScope",
    "RuleStatus",
    "build_adms_default_rules",
]
