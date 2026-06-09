from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from app.api.schemas import AddressIntelligenceResult

AddressValidationStatus = Literal["VALID", "PARTIAL", "INVALID", "MISSING"]
PostalMatchLevel = Literal["EXACT", "NEAR", "PARTIAL", "NONE", "UNKNOWN"]


STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

PUNCT_RE = re.compile(r"[^\w\s#\-/,]")
MULTISPACE_RE = re.compile(r"\s+")
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
ZIP_PLUS4_RE = re.compile(r"\b\d{5}-\d{4}\b")
STATE_RE = re.compile(r"\b([A-Z]{2})\b")
UNIT_RE = re.compile(r"\b(APT|STE|UNIT|#)\s*([A-Z0-9\-]+)\b")
PO_BOX_RE = re.compile(r"\bP\s*O\s*BOX\b|\bPO BOX\b")


@dataclass
class ParsedAddress:
    raw_address: Optional[str]
    standardized_address: Optional[str]
    address_line_1: Optional[str]
    address_line_2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postal_code: Optional[str]
    country: Optional[str]
    validation_status: AddressValidationStatus
    deliverable_flag: Optional[bool]
    address_confidence: float
    findings: List[str]
    postal_match_level: PostalMatchLevel = "UNKNOWN"


class AddressIntelligenceService:
    def __init__(self, default_country: str = "US"):
        self.default_country = default_country

    def validate(self, raw_address: Optional[str]) -> AddressIntelligenceResult:
        parsed = self._parse_and_standardize(raw_address)
        return AddressIntelligenceResult(**self._to_dict(parsed))

    def compare(
        self,
        addr_a: AddressIntelligenceResult,
        addr_b: AddressIntelligenceResult,
    ) -> str:
        if not addr_a.standardized_address or not addr_b.standardized_address:
            return "Address comparison unavailable."

        parsed_a = ParsedAddress(
            raw_address=addr_a.raw_address,
            standardized_address=addr_a.standardized_address,
            address_line_1=addr_a.address_line_1,
            address_line_2=addr_a.address_line_2,
            city=addr_a.city,
            state=addr_a.state,
            postal_code=addr_a.postal_code,
            country=addr_a.country,
            validation_status=addr_a.validation_status,
            deliverable_flag=addr_a.deliverable_flag,
            address_confidence=addr_a.address_confidence,
            findings=addr_a.findings,
            postal_match_level=addr_a.postal_match_level or "UNKNOWN",
        )

        parsed_b = ParsedAddress(
            raw_address=addr_b.raw_address,
            standardized_address=addr_b.standardized_address,
            address_line_1=addr_b.address_line_1,
            address_line_2=addr_b.address_line_2,
            city=addr_b.city,
            state=addr_b.state,
            postal_code=addr_b.postal_code,
            country=addr_b.country,
            validation_status=addr_b.validation_status,
            deliverable_flag=addr_b.deliverable_flag,
            address_confidence=addr_b.address_confidence,
            findings=addr_b.findings,
            postal_match_level=addr_b.postal_match_level or "UNKNOWN",
        )

        match_level = self._compare_parsed_addresses(parsed_a, parsed_b)
        return self._build_match_insight(parsed_a, parsed_b, match_level)

    def _parse_and_standardize(self, address: Optional[str]) -> ParsedAddress:
        if address is None or not str(address).strip():
            return ParsedAddress(
                raw_address=address,
                standardized_address=None,
                address_line_1=None,
                address_line_2=None,
                city=None,
                state=None,
                postal_code=None,
                country=self.default_country,
                validation_status="MISSING",
                deliverable_flag=None,
                address_confidence=0.0,
                findings=["Address missing"],
            )

        raw = str(address).strip()
        cleaned = self._clean_address(raw)
        standardized = self._standardize(cleaned)

        postal_code = self._extract_postal_code(standardized)
        state = self._extract_state(standardized)
        unit = self._extract_unit(standardized)

        segments = [seg.strip() for seg in standardized.split(",") if seg.strip()]
        address_line_1 = segments[0] if segments else standardized
        city = segments[1] if len(segments) >= 2 else None
        address_line_2 = unit

        findings: List[str] = []

        if standardized != raw.upper():
            findings.append("Address formatting normalized")

        if postal_code and not ZIP_PLUS4_RE.search(standardized):
            findings.append("Postal code present without ZIP+4")

        if PO_BOX_RE.search(standardized):
            findings.append("PO Box detected")

        has_street_number = bool(re.search(r"\b\d+\b", standardized))
        has_street_keyword = any(
            suffix in f" {standardized} "
            for suffix in [
                " ST ", " RD ", " AVE ", " BLVD ", " DR ",
                " LN ", " CT ", " PL ", " TER ", " PKWY ", " CIR ",
            ]
        )

        confidence = 0.0
        if standardized:
            confidence += 0.25
        if has_street_number:
            confidence += 0.20
        if has_street_keyword:
            confidence += 0.20
        if state:
            confidence += 0.15
        if postal_code:
            confidence += 0.20

        confidence = round(min(confidence, 1.0), 2)

        if confidence >= 0.85:
            validation_status: AddressValidationStatus = "VALID"
            deliverable_flag = True
        elif confidence >= 0.50:
            validation_status = "PARTIAL"
            deliverable_flag = None
            findings.append("Address may be incomplete or partially structured")
        else:
            validation_status = "INVALID"
            deliverable_flag = False
            findings.append("Address structure appears insufficient for reliable verification")

        if not state:
            findings.append("State missing or not recognized")
        if not postal_code:
            findings.append("Postal code missing")

        return ParsedAddress(
            raw_address=raw,
            standardized_address=standardized,
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            state=state,
            postal_code=postal_code,
            country=self.default_country,
            validation_status=validation_status,
            deliverable_flag=deliverable_flag,
            address_confidence=confidence,
            findings=self._dedupe(findings),
        )

    def _clean_address(self, value: str) -> str:
        v = value.upper().strip()
        v = PUNCT_RE.sub(" ", v)
        v = MULTISPACE_RE.sub(" ", v)
        return v.strip()

    def _standardize(self, value: str) -> str:
        v = value.upper().strip()

        suffix_patterns = {
            r"\bSTREET\b": "ST",
            r"\bROAD\b": "RD",
            r"\bAVENUE\b": "AVE",
            r"\bBOULEVARD\b": "BLVD",
            r"\bDRIVE\b": "DR",
            r"\bLANE\b": "LN",
            r"\bCOURT\b": "CT",
            r"\bPLACE\b": "PL",
            r"\bTERRACE\b": "TER",
            r"\bPARKWAY\b": "PKWY",
            r"\bCIRCLE\b": "CIR",
            r"\bAPARTMENT\b": "APT",
            r"\bSUITE\b": "STE",
        }

        directional_patterns = {
            r"\bNORTH\b": "N",
            r"\bSOUTH\b": "S",
            r"\bEAST\b": "E",
            r"\bWEST\b": "W",
            r"\bNORTHEAST\b": "NE",
            r"\bNORTHWEST\b": "NW",
            r"\bSOUTHEAST\b": "SE",
            r"\bSOUTHWEST\b": "SW",
        }

        for pattern, replacement in suffix_patterns.items():
            v = re.sub(pattern, replacement, v)

        for pattern, replacement in directional_patterns.items():
            v = re.sub(pattern, replacement, v)

        v = re.sub(r"\s*,\s*", ", ", v)
        v = MULTISPACE_RE.sub(" ", v).strip()

        return v.strip(" ,")

    def _extract_postal_code(self, value: str) -> Optional[str]:
        match = ZIP_RE.search(value)
        return match.group(0) if match else None

    def _extract_state(self, value: str) -> Optional[str]:
        candidates = STATE_RE.findall(value)
        for candidate in reversed(candidates):
            if candidate in STATE_CODES:
                return candidate
        return None

    def _extract_unit(self, value: str) -> Optional[str]:
        match = UNIT_RE.search(value)
        if not match:
            return None
        return f"{match.group(1)} {match.group(2)}"

    def _compare_parsed_addresses(
        self,
        a: ParsedAddress,
        b: ParsedAddress,
    ) -> PostalMatchLevel:
        if not a.standardized_address or not b.standardized_address:
            return "UNKNOWN"

        if a.standardized_address == b.standardized_address:
            return "EXACT"

        a_core = self._comparison_core(a.standardized_address)
        b_core = self._comparison_core(b.standardized_address)

        if a_core == b_core:
            return "EXACT"

        same_postal = bool(
            a.postal_code and b.postal_code and a.postal_code[:5] == b.postal_code[:5]
        )
        same_state = bool(a.state and b.state and a.state == b.state)

        tokens_a = set(a_core.split())
        tokens_b = set(b_core.split())

        if not tokens_a or not tokens_b:
            return "UNKNOWN"

        overlap = len(tokens_a.intersection(tokens_b))
        ratio = overlap / max(min(len(tokens_a), len(tokens_b)), 1)

        if same_postal and same_state and ratio >= 0.75:
            return "NEAR"

        if ratio >= 0.50:
            return "PARTIAL"

        return "NONE"

    def _comparison_core(self, value: str) -> str:
        stripped = ZIP_RE.sub("", value)
        stripped = UNIT_RE.sub("", stripped)
        stripped = MULTISPACE_RE.sub(" ", stripped).strip()
        return stripped

    def _build_match_insight(
        self,
        a: ParsedAddress,
        b: ParsedAddress,
        match_level: PostalMatchLevel,
    ) -> str:
        if match_level == "EXACT":
            return "Addresses normalize to the same standardized location."
        if match_level == "NEAR":
            return "Addresses appear highly similar after normalization and support the match."
        if match_level == "PARTIAL":
            return "Addresses share partial similarity but contain differences that may require review."
        if match_level == "NONE":
            return "Addresses do not materially support the match after normalization."
        return "Address evidence is incomplete or unavailable."

    def _to_dict(self, parsed: ParsedAddress) -> Dict[str, Any]:
        return {
            "raw_address": parsed.raw_address,
            "standardized_address": parsed.standardized_address,
            "address_line_1": parsed.address_line_1,
            "address_line_2": parsed.address_line_2,
            "city": parsed.city,
            "state": parsed.state,
            "postal_code": parsed.postal_code,
            "country": parsed.country,
            "validation_status": parsed.validation_status,
            "deliverable_flag": parsed.deliverable_flag,
            "address_confidence": parsed.address_confidence,
            "postal_match_level": parsed.postal_match_level,
            "findings": parsed.findings,
        }

    def _dedupe(self, values: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out