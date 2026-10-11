from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ProfileRuleExceptionMatch:
    exception_id: str
    rule_id: str
    rule_version: int
    field_name: str
    match_type: str
    match_value: Any
    reason: str


class ProfileRuleExceptionResolver:
    """
    Deterministic in-memory resolver for governed profile rule exceptions.

    Repository/database access belongs outside this class. The resolver
    receives already-authorized ACTIVE exceptions and performs only
    deterministic matching.
    """

    def __init__(
        self,
        exceptions: Iterable[dict[str, Any]],
    ) -> None:
        self._exact_index: dict[
            tuple[str, int, str, str],
            ProfileRuleExceptionMatch,
        ] = {}

        for exception in exceptions:
            self._add_exception(exception)

    @staticmethod
    def _canonical_exact_value(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _add_exception(
        self,
        exception: dict[str, Any],
    ) -> None:
        status = str(exception.get("status") or "").strip().upper()

        if status != "ACTIVE":
            return

        match_type = str(
            exception.get("match_type") or ""
        ).strip().upper()

        # Fail closed. V1 supports EXACT_VALUE only.
        if match_type != "EXACT_VALUE":
            raise ValueError(
                "Unsupported ACTIVE profile rule exception "
                f"match_type: {match_type or '<blank>'}."
            )

        exception_id = str(
            exception.get("exception_id") or ""
        ).strip()

        rule_id = str(
            exception.get("rule_id") or ""
        ).strip()

        field_name = str(
            exception.get("field_name") or ""
        ).strip()

        reason = str(
            exception.get("reason") or ""
        ).strip()

        if not exception_id.startswith("pre_"):
            raise ValueError(
                "ACTIVE profile rule exception has invalid "
                "exception_id."
            )

        if not rule_id.startswith("PROFILE_"):
            raise ValueError(
                "ACTIVE profile rule exception has invalid rule_id."
            )

        if not field_name:
            raise ValueError(
                "ACTIVE profile rule exception has no field_name."
            )

        if not reason:
            raise ValueError(
                "ACTIVE profile rule exception has no reason."
            )

        try:
            rule_version = int(exception.get("rule_version"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ACTIVE profile rule exception has invalid "
                "rule_version."
            ) from exc

        if rule_version < 1:
            raise ValueError(
                "ACTIVE profile rule exception has invalid "
                "rule_version."
            )

        raw_json = exception.get("match_value_json")

        if not isinstance(raw_json, str) or not raw_json:
            raise ValueError(
                "ACTIVE EXACT_VALUE exception has no "
                "match_value_json."
            )

        try:
            match_value = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ACTIVE EXACT_VALUE exception contains invalid "
                "match_value_json."
            ) from exc

        canonical_value = self._canonical_exact_value(match_value)

        key = (
            rule_id,
            rule_version,
            field_name,
            canonical_value,
        )

        match = ProfileRuleExceptionMatch(
            exception_id=exception_id,
            rule_id=rule_id,
            rule_version=rule_version,
            field_name=field_name,
            match_type=match_type,
            match_value=match_value,
            reason=reason,
        )

        existing = self._exact_index.get(key)

        if existing is not None:
            if existing.exception_id != exception_id:
                raise ValueError(
                    "Multiple ACTIVE exact-value exceptions exist "
                    "for the same rule/version/field/value."
                )
            return

        self._exact_index[key] = match

    def resolve_exact_value(
        self,
        *,
        rule_id: str,
        rule_version: int,
        field_name: str,
        observed_value: Any,
    ) -> ProfileRuleExceptionMatch | None:
        normalized_rule_id = str(rule_id or "").strip()
        normalized_field_name = str(field_name or "").strip()

        try:
            normalized_rule_version = int(rule_version)
        except (TypeError, ValueError):
            return None

        if (
            not normalized_rule_id
            or normalized_rule_version < 1
            or not normalized_field_name
        ):
            return None

        key = (
            normalized_rule_id,
            normalized_rule_version,
            normalized_field_name,
            self._canonical_exact_value(observed_value),
        )

        return self._exact_index.get(key)