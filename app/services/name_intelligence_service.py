from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Literal, Optional


NameMatchLevel = Literal[
    "EXACT",
    "SIMILAR",
    "DIFFERENT",
    "UNKNOWN",
]


COMMON_NICKNAMES: Dict[str, List[str]] = {
    "john": ["jon", "johnny", "jack"],
    "jon": ["john", "johnny"],
    "michael": ["mike", "mikey", "mick"],
    "mike": ["michael"],
    "matthew": ["matt", "mat"],
    "matt": ["matthew"],
    "william": ["bill", "billy", "will", "willy", "liam"],
    "bill": ["william"],
    "robert": ["rob", "bob", "bobby"],
    "bob": ["robert"],
    "richard": ["rich", "rick", "ricky", "dick"],
    "rick": ["richard"],
    "james": ["jim", "jimmy"],
    "jim": ["james"],
    "joseph": ["joe", "joey"],
    "joe": ["joseph"],
    "thomas": ["tom", "tommy"],
    "tom": ["thomas"],
    "daniel": ["dan", "danny"],
    "dan": ["daniel"],
    "anthony": ["tony"],
    "tony": ["anthony"],
    "elizabeth": ["liz", "beth", "lizzy", "eliza"],
    "liz": ["elizabeth"],
    "katherine": ["kate", "kathy", "kat", "katie"],
    "kate": ["katherine"],
    "margaret": ["maggie", "meg", "peggy"],
    "maggie": ["margaret"],
    "patricia": ["pat", "patty", "trish"],
    "pat": ["patricia"],
    "jennifer": ["jen", "jenny"],
    "jen": ["jennifer"],
    "christopher": ["chris"],
    "chris": ["christopher"],
    "steven": ["steve", "stephen"],
    "steve": ["steven", "stephen"],
    "stephen": ["steven", "steve"],
}


@dataclass(frozen=True)
class NameIntelligenceResult:
    raw_a: Optional[str]
    raw_b: Optional[str]
    normalized_a: Optional[str]
    normalized_b: Optional[str]
    similarity_score: float
    match_level: NameMatchLevel
    phonetic_key_a: Optional[str]
    phonetic_key_b: Optional[str]
    nickname_match: bool
    findings: List[str]
    insight: str


class NameIntelligenceService:
    """
    Stateless name-comparison utility.

    This service does not read, write, fetch, or persist tenant data, so it
    should not accept organization_id or perform tenant filtering itself.
    Tenant isolation must happen before tenant-owned records or names are
    passed into compare().
    """

    def compare(
        self,
        a: Optional[str],
        b: Optional[str],
    ) -> NameIntelligenceResult:
        norm_a = self._normalize(a)
        norm_b = self._normalize(b)

        if not norm_a or not norm_b:
            return NameIntelligenceResult(
                raw_a=a,
                raw_b=b,
                normalized_a=norm_a,
                normalized_b=norm_b,
                similarity_score=0.0,
                match_level="UNKNOWN",
                phonetic_key_a=(
                    self._phonetic_key(norm_a)
                    if norm_a
                    else None
                ),
                phonetic_key_b=(
                    self._phonetic_key(norm_b)
                    if norm_b
                    else None
                ),
                nickname_match=False,
                findings=[
                    "One or both names are missing."
                ],
                insight="Name comparison unavailable.",
            )

        findings: List[str] = []

        nickname_match = self._is_nickname_match(
            norm_a,
            norm_b,
        )

        phonetic_a = self._phonetic_key(norm_a)
        phonetic_b = self._phonetic_key(norm_b)

        if norm_a == norm_b:
            findings.append(
                "Names match exactly after normalization."
            )

            score = 1.0
            match_level: NameMatchLevel = "EXACT"
            insight = "Names match exactly."

        else:
            seq_score = SequenceMatcher(
                None,
                norm_a,
                norm_b,
            ).ratio()

            score = seq_score

            if nickname_match:
                score = max(score, 0.92)
                findings.append(
                    "Known nickname or common name variant detected."
                )

            if (
                phonetic_a
                and phonetic_b
                and phonetic_a == phonetic_b
            ):
                score = max(score, 0.88)
                findings.append(
                    "Names are phonetically similar."
                )

            if self._one_char_off(
                norm_a,
                norm_b,
            ):
                score = max(score, 0.90)
                findings.append(
                    "Names differ by a minor spelling variation."
                )

            score = round(
                min(
                    max(score, 0.0),
                    1.0,
                ),
                4,
            )

            if score >= 0.97:
                match_level = "EXACT"
                insight = (
                    "Names are effectively identical "
                    "after normalization."
                )

            elif score >= 0.82:
                match_level = "SIMILAR"
                insight = (
                    "Names appear to be a likely spelling "
                    "or nickname variation."
                )

            else:
                match_level = "DIFFERENT"
                insight = (
                    "Names do not strongly support a match."
                )

        return NameIntelligenceResult(
            raw_a=a,
            raw_b=b,
            normalized_a=norm_a,
            normalized_b=norm_b,
            similarity_score=score,
            match_level=match_level,
            phonetic_key_a=phonetic_a,
            phonetic_key_b=phonetic_b,
            nickname_match=nickname_match,
            findings=findings,
            insight=insight,
        )

    @staticmethod
    def _normalize(
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalized = value.strip().lower()

        if not normalized:
            return None

        normalized = re.sub(
            r"[^a-z\s\-']",
            "",
            normalized,
        )
        normalized = re.sub(
            r"\s+",
            " ",
            normalized,
        ).strip()

        return normalized or None

    @staticmethod
    def _is_nickname_match(
        a: str,
        b: str,
    ) -> bool:
        return (
            b in COMMON_NICKNAMES.get(a, [])
            or a in COMMON_NICKNAMES.get(b, [])
        )

    @staticmethod
    def _one_char_off(
        a: str,
        b: str,
    ) -> bool:
        if abs(len(a) - len(b)) > 1:
            return False

        return (
            SequenceMatcher(
                None,
                a,
                b,
            ).ratio()
            >= 0.85
        )

    @staticmethod
    def _phonetic_key(
        value: Optional[str],
    ) -> Optional[str]:
        if not value:
            return None

        normalized = value.lower()
        normalized = re.sub(
            r"[^a-z]",
            "",
            normalized,
        )

        if not normalized:
            return None

        replacements = [
            ("ph", "f"),
            ("ck", "k"),
            ("ght", "t"),
            ("gh", ""),
            ("kn", "n"),
            ("wr", "r"),
            ("wh", "w"),
            ("dg", "j"),
            ("tch", "ch"),
            ("q", "k"),
            ("x", "ks"),
            ("z", "s"),
            ("v", "f"),
        ]

        for source, target in replacements:
            normalized = normalized.replace(
                source,
                target,
            )

        if len(normalized) > 1:
            first = normalized[0]
            tail = re.sub(
                r"[aeiouy]",
                "",
                normalized[1:],
            )
            normalized = first + tail

        normalized = re.sub(
            r"(.)\1+",
            r"\1",
            normalized,
        )

        return normalized[:6]