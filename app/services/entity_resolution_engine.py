from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from rapidfuzz.distance import JaroWinkler
from typing import Any, Optional
from app.services.similarity_engine import (SimilarityEngine,email_domain_trust,)

from app.api.schemas import MatchExplainRequest


@dataclass
class SignalScore:
    name: str
    score: float
    weight: float
    weighted_score: float
    detail: str
    signal_band: str
    match_level: Optional[str] = None
    domain_trust: Optional[float] = None
    signal_type: Optional[str] = None



class EntityResolutionEngine:
    """
    Computes a multi-signal entity resolution score for two records.

    Signals included:
    - name similarity
    - dob / registration / effective date match
    - email / identifier match
    - address / attribute similarity
    - source trust
    - steward learning adjustment
    - policy risk adjustment

    Final outputs:
    - raw_entity_score: 0..1
    - decision_confidence_score: 0..100
    - signal_weights
    - signal_contributions
    - automation_tier
    - automation_readiness_score: 0..100
    - automation_readiness_label
    - automation_policy_status
    - final_recommended_action
    """

    DEFAULT_WEIGHTS = {
        "member_id_match": 0.35,
        "name_similarity": 0.10,
        "dob_match": 0.20,
        "email_match": 0.20,
        "address_similarity": 0.10,
        "attribute_similarity": 0.10,
        "source_trust": 0.05,
        "steward_learning": 0.05,
    }

    DEFAULT_SOURCE_TRUST_MAP = {
        "MDM": 1.00,
        "ERP": 0.90,
        "CRM": 0.85,
        "PLM": 0.85,
        "PIM": 0.85,
        "SUPPLIER_PORTAL": 0.75,
        "LEGACY": 0.60,
        "BOCA SOUTH": 0.85,
        "BOCA WEST": 0.85,
        "CERNER SOUTH": 0.90,
    }

    DEFAULT_AUTOMATION_THRESHOLDS = {
        "auto_merge_ready_min": 95,
        "suggested_merge_min": 85,
        "review_advised_min": 65,
    }

    DEFAULT_SIGNAL_TONE_THRESHOLDS = {
        "strong_positive": 0.97,
        "positive": 0.95,
        "neutral_name": 0.78,
        "neutral_address": 0.75,
        "neutral_source": 0.65,
        "warning": 0.60,
        "high_impact": 0.85,
    }

    DEFAULT_READINESS_LABEL_THRESHOLDS = {
        "high": 85,
        "moderate": 60,
    }

    DOMAIN_SIGNAL_WEIGHTS = {
        "CUSTOMER": {
           "member_id_match": 0.25,
            "name_similarity": 0.10,
            "dob_match": 0.25,
            "email_match": 0.20,
            "address_similarity": 0.10,
            "source_trust": 0.05,
            "steward_learning": 0.05,
        },
        "SUPPLIER": {
            "supplier_id_match": 0.45,
            "tax_id_match": 0.25,
            "name_similarity": 0.10,
            "contact_email_match": 0.05,
            "address_similarity": 0.05,
            "source_trust": 0.05,
            "steward_learning": 0.05,
        },
        "PRODUCT": {
            "product_id_match": 0.20,
            "gtin_match": 0.25,
            "sku_match": 0.15,
            "name_similarity": 0.10,
            "effective_lot_date_match": 0.10,
            "attribute_similarity": 0.10,
            "source_trust": 0.05,
            "steward_learning": 0.05,
        },
        "PROVIDER": {
            "provider_id_match": 0.25,
            "npi_match": 0.25,
            "name_similarity": 0.10,
            "provider_email_match": 0.10,
            "address_similarity": 0.20,
            "specialty_similarity": 0.05,
            "source_trust": 0.03,
            "steward_learning": 0.02,
},
        "PATIENT": {
            "patient_id_match": 0.25,
            "human_id_match": 0.05,
            "dob_match": 0.20,
            "name_similarity": 0.20,
            "address_similarity": 0.15,
            "email_match": 0.10,
            "source_trust": 0.05,
            "steward_learning": 0.00,
        },
    }

    def _gtin_detail(
        self,
        gtin_a: Optional[str],
        gtin_b: Optional[str],
    ) -> str:

        if not gtin_a or not gtin_b:
            return "GTIN identifier missing on one or both records."

        if self._normalize_text(gtin_a) == self._normalize_text(gtin_b):
            return "GTIN identifiers match exactly."

        return "GTIN identifiers partially align or differ."
    
    def _sku_detail(
        self,
        sku_a: Optional[str],
        sku_b: Optional[str],
    ) -> str:
        if not sku_a or not sku_b:
            return "SKU evidence missing on one or both product records."

        if self._normalize_text(sku_a) == self._normalize_text(sku_b):
            return "SKU values match exactly."

        return "SKU values differ across product records."
    
    def _supplier_id_detail(self, a, b):
        return f"Compared Supplier IDs '{a}' and '{b}'."
    
    def _specialty_detail(
        self,
        specialty_a: str | None,
        specialty_b: str | None,
    ) -> str:

        if not specialty_a or not specialty_b:
            return (
                "Provider specialty evidence was unavailable "
                "for comparison."
            )

        if (
            self._normalize_text(specialty_a)
            == self._normalize_text(specialty_b)
        ):
            return (
                f"Compared provider specialties "
                f"'{specialty_a}' and '{specialty_b}'. "
                "Clinical specialties aligned exactly."
            )

        return (
            f"Compared provider specialties "
            f"'{specialty_a}' and '{specialty_b}'. "
            "Specialty classifications differed across records."
        )
    
    def _product_id_detail(self, a, b):
        return f"Compared product IDs '{a}' and '{b}'."
    
    def _tax_id_detail(self, a, b):
        return f"Compared tax IDs '{a}' and '{b}'."
    
    def _provider_id_detail(self, a, b):
        return f"Compared Provider IDs '{a}' and '{b}'."
    
    def _patient_id_detail(self, a, b):
        return f"Compared Patient IDs '{a}' and '{b}'."

    def _human_id_detail(self, a, b):

        if not a and not b:
            return (
                "Immutable human ID evidence was unavailable "
                "for comparison."
            )

        if not a or not b:
            return (
                "Immutable human ID missing on one record."
            )

        if self._normalize_text(a) == self._normalize_text(b):
            return (
                f"Compared Immutable human IDs '{a}' and '{b}'. "
                "Immutable identity anchor matched exactly."
            )

        return (
            f"Compared Immutable human IDs '{a}' and '{b}'. "
            "Immutable identity anchors differed."
        )
    
    def _npi_detail( 
        self, 
        npi_a: str | None, 
        npi_b: str | None,
        ) -> str:

        if not npi_a or not npi_b:
            return "NPI evidence was unavailable for comparison."

        if str(npi_a).strip() == str(npi_b).strip():
            return (
                f"Compared provider NPIs '{npi_a}' and '{npi_b}'. "
                "National Provider Identifier values matched exactly."
            )

        return (
            f"Compared provider NPIs '{npi_a}' and '{npi_b}'. "
            "Provider registry identifiers did not align."
        )
    
    def _effective_lot_date_detail(self, a, b):
       return f"Compared effective / lot dates '{a}' and '{b}'."
    
    def _attribute_similarity_detail(self, a, b):
        return ("Compared product descriptors, classifications, and attributes"
                "to assess overall product similarity beyond exact identifier matches."
            )   
    def _address_similarity_detail(self, a, b):
        return ("Addresses normalized to the same standardized location "
                "with high similarity confidence."
            )   

    def __init__(self) -> None:
        self.default_weights = self.DEFAULT_WEIGHTS.copy()
        self.similarity_engine = SimilarityEngine()
        self.default_source_trust_map = self.DEFAULT_SOURCE_TRUST_MAP.copy()
        self.default_automation_thresholds = self.DEFAULT_AUTOMATION_THRESHOLDS.copy()
        self.default_signal_tone_thresholds = self.DEFAULT_SIGNAL_TONE_THRESHOLDS.copy()
        self.default_readiness_label_thresholds = (
        self.DEFAULT_READINESS_LABEL_THRESHOLDS.copy()
        )

    def _product_attribute_similarity(
        self,
        record_a,
        record_b,
        ) -> float:
            scores = []

            product_name_score = self._similarity(
                getattr(record_a, "product_name", None),
                getattr(record_b, "product_name", None),
        )

            variant_score = self._similarity(
                getattr(record_a, "product_variant", None),
                getattr(record_b, "product_variant", None),
        )

            pack_score = self._similarity(
                getattr(record_a, "pack_size", None),
                getattr(record_b, "pack_size", None),
        )

            sku_score = self._similarity(
                getattr(record_a, "sku", None),
                getattr(record_b, "sku", None),
        )

            scores.extend([
                product_name_score,
                variant_score,
                pack_score,
                sku_score,
        ])
            valid_scores = [s for s in scores if s > 0]

            if not valid_scores:
                return 0.0

            return round(sum(valid_scores) / len(valid_scores), 4)
    
    def _resolve_entity_id(self, record, domain: str) -> str:
        domain = (domain or "CUSTOMER").upper()

        fields_by_domain = {
            "CUSTOMER": ["member_id"],
            "PATIENT": ["patient_id", "member_id"],
            "PROVIDER": ["provider_id", "npi", "member_id"],
            "SUPPLIER": ["supplier_id", "vendor_id", "tax_id", "member_id"],
            "PRODUCT": ["product_id", "gtin", "sku", "upc", "member_id"],
        }

        for field in fields_by_domain.get(domain, ["member_id"]):
            value = getattr(record, field, None)
            if value is not None and str(value).strip():
                return str(value).strip()

        return ""
            

    def score(
        self,
        req: MatchExplainRequest,
        address_similarity_score: Optional[float] = None,
        override_rate_estimate: Optional[float] = None,
        composite_risk_score: Optional[int] = None,
        risk_flag: Optional[str] = None,
        recommended_action: Optional[str] = None,
        address_match_insight: Optional[str] = None,
        composite_risk_band: Optional[str] = None,
        primary_risk_driver: Optional[str] = None,
        policy_config: Optional[dict[str, Any]] = None,
        

    ) -> dict[str, Any]:   
        provider_email_score = 0.0
        provider_email_a = None
        provider_email_b = None
        specialty_score = 0.0

        source_trust_map = self._resolve_source_trust_map(policy_config)
        automation_thresholds = self._resolve_automation_thresholds(policy_config)
        signal_tone_thresholds = self._resolve_signal_tone_thresholds(policy_config)
        readiness_label_thresholds = self._resolve_readiness_label_thresholds(policy_config
        )
        record_a = req.record_a
        record_b = req.record_b

        domain = (req.domain or "CUSTOMER").upper()
        normalized_domain = domain.strip().upper()
        domain_weights = self.get_domain_signal_weights(domain)

        record_a_id = self._resolve_entity_id(record_a, normalized_domain)
        record_b_id = self._resolve_entity_id(record_b, normalized_domain)


        member_id_score = self._member_id_match(
        record_a_id,
        record_b_id,
    )
        source_score = self._source_trust_score(
            record_a.source_system,
            record_b.source_system,
            source_trust_map,
        )

        name_score = self._name_similarity(
            record_a.first_name,
            record_a.last_name,
            record_b.first_name,
            record_b.last_name,
        )


        dob_score = self._dob_match(record_a.dob, record_b.dob)
        trust_multiplier = 1.0
            # ---------------------------------------------------------
            # Email Similarity + Trust Evaluation
            # ---------------------------------------------------------

        email_a = (record_a.email or "").strip().lower()
        email_b = (record_b.email or "").strip().lower()

            # Optional future normalization hook
            # email_a = SimilarityEngine.normalize_email(email_a)
            # email_b = SimilarityEngine.normalize_email(email_b)

        email_similarity = SimilarityEngine.email_similarity(
                email_a,
                email_b,
            )   

            # ---------------------------------------------------------
            # Domain Extraction
            # ---------------------------------------------------------

        domain_a = ""
        domain_b = ""

        if "@" in email_a:
            domain_a = email_a.split("@")[1]

        if "@" in email_b:
            domain_b = email_b.split("@")[1]

            # ---------------------------------------------------------
            # Domain Trust Evaluation
            # ---------------------------------------------------------

        domain_trust_a = email_domain_trust(domain_a)
        domain_trust_b = email_domain_trust(domain_b)

        # Conservative approach:
        # use lower trust domain to avoid overstating confidence
        domain_trust_score = min(domain_trust_a, domain_trust_b)

        
        # ---------------------------------------------------------
        # Composite Email Score
        # ---------------------------------------------------------

        email_score = min(
            email_similarity * domain_trust_score,
            1.0,
        )
        # ---------------------------------------------------------
        # Explainability Classification
        # ---------------------------------------------------------

        email_match_level = "DIFFERENT"

        if email_score >= 0.99:
            email_match_level = "EXACT"

        elif email_score >= 0.90:
            email_match_level = "SIMILAR"

        elif email_score >= 0.70:
            email_match_level = "FUZZY"

       

        address_score = SimilarityEngine.address_similarity(
            getattr(record_a, "address", ""),
            getattr(record_b, "address", ""),
        )

        product_id_score = SimilarityEngine.product_id_similarity(
            getattr(record_a, "product_id", None),
            getattr(record_b, "product_id", None),
        )
        
        gtin_score = (
                1.0
                if self._normalize_text(getattr(record_a, "gtin", None))
                and self._normalize_text(getattr(record_a, "gtin", None))
                == self._normalize_text(getattr(record_b, "gtin", None))
                else 0.0
            )
        
        sku_score = self._similarity(
                getattr(record_a, "sku", None),
                getattr(record_b, "sku", None),
            )
        
        supplier_id_a = (
                    getattr(record_a, "supplier_id", None)
                    or getattr(record_a, "member_id", None)
                )

        supplier_id_b = (
                        getattr(record_b, "supplier_id", None)
                        or getattr(record_b, "member_id", None)
                )

        supplier_id_score = (
            1.0
            if self._normalize_text(supplier_id_a)
            and self._normalize_text(supplier_id_a)
            == self._normalize_text(supplier_id_b)
            else 0.0
        )

        tax_id_score = (
                1.0
                if self._normalize_text(getattr(record_a, "tax_id", None))
                and self._normalize_text(getattr(record_a, "tax_id", None))
                == self._normalize_text(getattr(record_b, "tax_id", None))
                else 0.0
            )

        provider_id_score = (
            1.0
            if self._normalize_text(getattr(record_a, "provider_id", None))
            and self._normalize_text(getattr(record_a, "provider_id", None))
            == self._normalize_text(getattr(record_b, "provider_id", None))
            else 0.0
        )

        npi_score = (
            1.0
            if self._normalize_text(getattr(record_a, "npi", None))
            and self._normalize_text(getattr(record_a, "npi", None))
            == self._normalize_text(getattr(record_b, "npi", None))
            else 0.0
        )

        patient_id_a = getattr(record_a, "patient_id", None)
        patient_id_b = getattr(record_b, "patient_id", None)

        patient_id_a_norm = self._normalize_text(patient_id_a)
        patient_id_b_norm = self._normalize_text(patient_id_b)

        patient_id_a_base = patient_id_a_norm.split("-")[0]
        patient_id_b_base = patient_id_b_norm.split("-")[0]

        if patient_id_a_norm and patient_id_a_norm == patient_id_b_norm:
            patient_id_score = 1.0
        elif patient_id_a_base and patient_id_a_base == patient_id_b_base:
            patient_id_score = 0.90
        else:
            patient_id_score = self._similarity(patient_id_a, patient_id_b)

        human_id_a = getattr(record_a, "human_id", None)
        human_id_b = getattr(record_b, "human_id", None)

        human_id_a_norm = self._normalize_text(human_id_a)
        human_id_b_norm = self._normalize_text(human_id_b)

        # BOTH missing = unknown evidence
        if not human_id_a_norm and not human_id_b_norm:
            human_id_score = None

        # ONE missing = incomplete evidence
        elif not human_id_a_norm or not human_id_b_norm:
            human_id_score = None

        # Exact match
        elif human_id_a_norm == human_id_b_norm:
            human_id_score = 1.0

        # True mismatch
        else:
            human_id_score = 0.0
        
        effective_lot_date_score = (
                1.0
                if self._normalize_text(getattr(record_a, "effective_lot_date", None))
                and self._normalize_text(getattr(record_a, "effective_lot_date", None))
                == self._normalize_text(getattr(record_b, "effective_lot_date", None))
                else 0.0
            )
        
        normalized_domain = (domain or "").strip().upper()
        
        match_score = (
            name_score * 0.30 +
            email_score * 0.25 +
            address_score * 0.20 +
            dob_score * 0.15 +
            member_id_score * 0.10
            )
        match_score = round(match_score, 2)
        

        attribute_similarity_score = address_score
        learning_adjustment = self._learning_adjustment(override_rate_estimate)
        learning_score = max(0.0, min(1.0, 1.0 + learning_adjustment))
        risk_multiplier = self._risk_multiplier(composite_risk_score)
        
        signals = []
        
        if normalized_domain == "CUSTOMER":

            full_name_a = (
            f"{getattr(record_a, 'first_name', '') or ''} "
            f"{getattr(record_a, 'last_name', '') or ''}"
            .strip()
            .lower()
        )

            full_name_b = (
            f"{getattr(record_b, 'first_name', '') or ''} "
            f"{getattr(record_b, 'last_name', '') or ''}"
            .strip()
            .lower()
        )

            name_similarity_score = SequenceMatcher(
            None,
            full_name_a,
            full_name_b,
        )   .ratio()

            match_score = (
            name_similarity_score * 0.25 +
            email_score * 0.15 +
            address_score * 0.20 +
            dob_score * 0.25 +
            member_id_score * 0.10 +
            source_score * 0.05
        )
            if (
            dob_score == 1.0
            and address_score >= 0.95
            and name_similarity_score >= 0.85
            ):
                match_score = max(match_score, 0.82)

            elif (
            dob_score == 1.0
            and name_similarity_score >= 0.90
        ):
                match_score = max(match_score, 0.78)
            
            signals.append(
                self._build_signal(
                    "name_similarity",
                    name_similarity_score,
                    domain_weights.get("name_similarity", 0.0),
                    (
                        "Names align closely across records."
                        if name_similarity_score >= 0.90
                        else "Names partially align."
                ),
        )
    )

            signals.append(
                self._build_signal(
                    "member_id_match",
                    member_id_score,
                    domain_weights.get("member_id_match", 0.0),
                    self._member_id_detail(
                        record_a_id,
                        record_b_id,
                    ),
                )
        )

            signals.append(
                self._build_signal(
                    "dob_match",
                    dob_score,
                    domain_weights.get("dob_match", 0.0),
                    self._dob_detail(
                        record_a.dob,
                        record_b.dob,
                    ),
                )
            )

            signals.append(
                self._build_signal(
                    "address_similarity",
                    address_score,
                    domain_weights.get("address_similarity", 0.0),
                    self._address_detail(
                            record_a.address,
                            record_b.address,
                            address_score,
                            domain,
                            address_match_insight,
                        ),
                        signal_type="probabilistic",
                    )
            )

            signals.append(
                self._build_signal(
                    "email_match",
                    email_score,
                    domain_weights.get("email_match", 0.0),
                    self._email_detail(
                            record_a.email,
                            record_b.email,
                        ),
                        match_level=email_match_level,
                        domain_trust=domain_trust_score,
                        signal_type="probabilistic",
                )
            )

            signals.append(
                self._build_signal(
                    "source_trust",
                    source_score,
                    domain_weights.get("source_trust", 0.0),
                    self._source_detail(
                        record_a.source_system,
                        record_b.source_system,
                    ),
                    signal_type="probabilistic",
                )
            )
            raw_entity_score = round(match_score * risk_multiplier, 4)

        elif normalized_domain == "PRODUCT":

            signals = []

            product_id_a = getattr(record_a, "product_id", None)
            product_id_b = getattr(record_b, "product_id", None)

            product_name_a = getattr(record_a, "product_name", None)
            product_name_b = getattr(record_b, "product_name", None)

            product_variant_a = getattr(record_a, "product_variant", None)
            product_variant_b = getattr(record_b, "product_variant", None)

            effective_lot_date_a = getattr(record_a, "effective_lot_date", None)
            effective_lot_date_b = getattr(record_b, "effective_lot_date", None)

            gtin_a = getattr(record_a, "gtin", None)
            gtin_b = getattr(record_b, "gtin", None)

            sku_a = getattr(record_a, "sku", None)
            sku_b = getattr(record_b, "sku", None)

            product_id_score = self._similarity(product_id_a, product_id_b)

            product_name_score = self._similarity(product_name_a, product_name_b)

            gtin_score = (
                    1.0
                    if self._normalize_text(gtin_a)
                    and self._normalize_text(gtin_a) == self._normalize_text(gtin_b)
                    else 0.0
                )

            sku_score = self._similarity(sku_a, sku_b)

            effective_lot_date_score = (
                    1.0
                    if self._normalize_text(effective_lot_date_a)
                    and self._normalize_text(effective_lot_date_a)
                    == self._normalize_text(effective_lot_date_b)
                    else 0.0
                )

            attribute_similarity_score = self._product_attribute_similarity(
                    record_a,
                    record_b,
                )

    # Use product name score as the domain-specific name signal
            name_score = product_name_score

            match_score = (
                product_id_score * domain_weights.get("product_id_match", 0.0)
                        + gtin_score * domain_weights.get("gtin_match", 0.0)
                        + sku_score * domain_weights.get("sku_match", 0.0)
                        + product_name_score * domain_weights.get("name_similarity", 0.0)
                        + effective_lot_date_score * domain_weights.get(
                            "effective_lot_date_match",
                                0.0,
                            )
                + attribute_similarity_score * domain_weights.get(
                    "attribute_similarity",
                    0.0,
                )
                + source_score * domain_weights.get("source_trust", 0.0)
                + learning_score * domain_weights.get("steward_learning", 0.0)
            )

            match_score = round(match_score, 4)

            signals.append(
                    self._build_signal(
                        "product_id_match",
                        product_id_score,
                        domain_weights.get("product_id_match", 0.0),
                        self._product_id_detail(product_id_a, product_id_b),
                        signal_type="deterministic",
                    )
                )

            signals.append(
                    self._build_signal(
                        "gtin_match",
                        gtin_score,
                        domain_weights.get("gtin_match", 0.0),
                        self._gtin_detail(gtin_a, gtin_b),
                        signal_type="deterministic",
                    )
                )

            signals.append(
                    self._build_signal(
                        "sku_match",
                        sku_score,
                        domain_weights.get("sku_match", 0.0),
                        self._sku_detail(sku_a, sku_b),
                        signal_type="deterministic",
                    )
                )

            signals.append(
                    self._build_signal(
                        "name_similarity",
                        product_name_score,
                        domain_weights.get("name_similarity", 0.0),
                        self._name_detail(
                            product_name_a,
                            product_variant_a,
                            product_name_b,
                            product_variant_b,
                        ),
                signal_type="probabilistic",
                )
            )

            signals.append(
                self._build_signal(
                        "effective_lot_date_match",
                        effective_lot_date_score,
                        domain_weights.get("effective_lot_date_match", 0.0),
                        self._effective_lot_date_detail(
                            effective_lot_date_a,
                            effective_lot_date_b,
                        ),
                    signal_type="deterministic",
                    )
                )

            signals.append(
                    self._build_signal(
                        "attribute_similarity",
                        attribute_similarity_score,
                        domain_weights.get("attribute_similarity", 0.0),
                        self._attribute_similarity_detail(record_a, record_b),
                        signal_type="probabilistic",
                    )
                )

            signals.append(
                    self._build_signal(
                        "source_trust",
                        source_score,
                        domain_weights.get("source_trust", 0.0),
                        self._source_detail(
                            record_a.source_system,
                            record_b.source_system,
                        ),
                        signal_type="probabilistic",
                    )
                )

            raw_entity_score = round(match_score * risk_multiplier, 4)
            

        elif normalized_domain == "SUPPLIER":

            signals = []

            
            supplier_address_similarity_score = address_score

            match_score = (
                supplier_id_score     * 0.50 +
                tax_id_score          * 0.25 +
                name_score            * 0.10 +
                email_score           * 0.05 +
                address_score         * 0.05 +
                source_score          * 0.05
            )
            match_score = round(match_score, 2)

            if supplier_id_score == 1.0 and tax_id_score == 1.0:
                match_score = max(match_score, 0.98)

            elif supplier_id_score == 1.0:
                match_score = max(match_score, 0.94)

            elif tax_id_score == 1.0:
                match_score = max(match_score, 0.90)
            

            signals.append(
                self._build_signal(
                    "supplier_id_match",
                    supplier_id_score,
                    domain_weights.get("supplier_id_match", 0.0),
                    self._supplier_id_detail(
                    supplier_id_a,
                    supplier_id_b,
                ),
            )
        )

            signals.append(
                self._build_signal(
                    "address_similarity",
                    supplier_address_similarity_score,
                    domain_weights.get("address_similarity", 0.0),
                    self._address_detail(
                    record_a.address,
                    record_b.address,
                    address_score,
                    domain,
                    address_match_insight,
                )
            )
        )

            signals.append(
                self._build_signal(
                    "tax_id_match",
                    tax_id_score,
                    domain_weights.get("tax_id_match", 0.0),
                    self._tax_id_detail(
                        getattr(record_a, "tax_id", None),
                        getattr(record_b, "tax_id", None),
                    ),
                )
            )

            signals.append(
                self._build_signal(
                    "contact_email_match",
                    email_score,
                    domain_weights.get("contact_email_match", 0.0),
                    self._contact_email_detail(
                        record_a.email,
                        record_b.email,
                    ),

                    match_level=email_match_level,
                    domain_trust=domain_trust_score,
                    signal_type="probabilistic",
                )
            )
            signals.append(
                self._build_signal(
                    "source_trust",
                    source_score,
                    domain_weights.get("source_trust", 0.0),
                    self._source_detail(
                        record_a.source_system,
                        record_b.source_system,
            ),
        )
)
            raw_entity_score = round(match_score * risk_multiplier, 4)
        
        elif normalized_domain == "PROVIDER":
            
            signals = []

            print("===== PROVIDER RECORD A =====")
            print(record_a)

            print("===== PROVIDER RECORD B =====")
            print(record_b)

            print("===== PROVIDER RECORD A DICT =====")
            print(vars(record_a))

            print("===== PROVIDER RECORD B DICT =====")
            print(vars(record_b))
      
            provider_email_score = 0.0

            specialty_score = self._similarity(
            getattr(record_a, "specialty", None),
            getattr(record_b, "specialty", None),
        )

            # ---------------------------------------------------------
            # Provider Email Evaluation
            # ---------------------------------------------------------

            provider_email_match_level = "DIFFERENT"
            provider_domain_trust_score = 0.0

            provider_email_a = (
            getattr(record_a, "provider_email", None)
            or getattr(record_a, "email", None)
            or ""
            ).strip().lower()

            provider_email_b = (
                    getattr(record_b, "provider_email", None)
                    or getattr(record_b, "email", None)
                    or ""
            ).strip().lower()

        if provider_email_a and provider_email_b:
                    
            provider_email_similarity = (SimilarityEngine.email_similarity(
                    provider_email_a,
                    provider_email_b,
                    )
                )
            provider_email_score = round(
            provider_email_similarity,
                    4,
    )
                

            # -----------------------------------------------------
            # Domain Extraction
            # -----------------------------------------------------

            provider_domain_a = ""
            provider_domain_b = ""

            if "@" in provider_email_a:
                    provider_domain_a = provider_email_a.split("@")[1]

            if "@" in provider_email_b:
                        provider_domain_b = provider_email_b.split("@")[1]

            # -----------------------------------------------------
            # Domain Trust Evaluation
            # -----------------------------------------------------

            provider_domain_trust_a = email_domain_trust(provider_domain_a)
            provider_domain_trust_b = email_domain_trust(provider_domain_b)

                # Conservative trust model
            provider_domain_trust_score = min(
            provider_domain_trust_a,
            provider_domain_trust_b,
                )

            # -----------------------------------------------------
            # Composite Email Score
            # -----------------------------------------------------

            provider_email_score = min(
                provider_email_similarity * provider_domain_trust_score,
                1.0,
                )

            # -----------------------------------------------------
            # Explainability Classification
            # -----------------------------------------------------

            if provider_email_score >= 0.99:
                                provider_email_match_level = "EXACT"

            elif provider_email_score >= 0.90:
                                provider_email_match_level = "SIMILAR"

            elif provider_email_score >= 0.70:
                                provider_email_match_level = "FUZZY" 

            provider_name_a = " ".join([
                    getattr(record_a, "provider_first_name", "") or "",
                    getattr(record_a, "provider_last_name", "") or "",
                ]).strip()

            provider_name_b = " ".join([
                    getattr(record_b, "provider_first_name", "") or "",
                    getattr(record_b, "provider_last_name", "") or "",
                ]).strip()

            name_score = self._similarity(
                    provider_name_a,
                    provider_name_b,
                )

            print("PROVIDER FIELD DEBUG")
            print("provider_name_a:", provider_name_a)
            print("provider_name_b:", provider_name_b)
            print("OVERRIDDEN name_score:", name_score)

            print(
                    "provider_address_a:",
                    getattr(record_a, "provider_address", None),
                )

            print(
                    "provider_address_b:",
                    getattr(record_b, "provider_address", None),
                )

            print("OVERRIDDEN address_score:", address_score)
            print("PROVIDER ADDRESS SCORE:", address_score)
            print("RECORD A ADDRESS:", record_a.address)
            print("RECORD B ADDRESS:", record_b.address)
                                
                #specialty score----------------------------
            specialty_score = self._similarity(
                getattr(record_a, "specialty", None),
                getattr(record_b, "specialty", None),
                )
                #----------------------------------------------------
            provider_address_a = (
                    getattr(record_a, "provider_address", None)
                    or getattr(record_a, "address", None)
                )

            provider_address_b = (
                    getattr(record_b, "provider_address", None)
                    or getattr(record_b, "address", None)
                )

            address_score = self._similarity(
                        provider_address_a,
                        provider_address_b,
                )


            match_score = (
                    provider_id_score * 0.25 +
                    npi_score * 0.25 +
                    name_score * 0.10 +
                    provider_email_score * 0.10 +
                    address_score * 0.20 +
                    specialty_score * 0.05 +
                    source_score * 0.03 +
                    learning_score * 0.02
                )

                # -----------------------------------------------------
                # Signal Registration
                # -----------------------------------------------------

            signals.append(
                            self._build_signal(
                                "provider_id_match",
                                provider_id_score,
                                domain_weights.get("provider_id_match", 0.0),
                                self._provider_id_detail(
                                    getattr(record_a, "provider_id", None),
                                    getattr(record_b, "provider_id", None),
                                ),
                            )
                        )

            signals.append(
                        self._build_signal(
                            "provider_email_match",
                            provider_email_score,
                            domain_weights.get("provider_email_match", 0.0),
                            self._email_detail(
                                provider_email_a,
                                provider_email_b,
                            ),
                            match_level=provider_email_match_level,
                            domain_trust=provider_domain_trust_score,
                            signal_type="probabilistic",
                        )
                    )
         
            signals.append(
                        self._build_signal(
                            "address_similarity",
                            address_score,
                            domain_weights.get("address_similarity", 0.0),
                            self._address_detail(
                                provider_address_a,
                                provider_address_b,
                                address_score,
                                domain,
                                address_match_insight,
                            ),
                            signal_type="probabilistic",
                        )
                    )
                
            signals.append(
                        self._build_signal(
                            "npi_match",
                            npi_score,
                            domain_weights.get("npi_match", 0.0),
                            self._npi_detail(
                                getattr(record_a, "npi", None),
                                getattr(record_b, "npi", None),
                            ),
                            signal_type="deterministic",
                        )
                    )
            signals.append(
                    self._build_signal(
                        "name_similarity",
                        name_score,
                        domain_weights.get("name_similarity", 0.0),
                        self._name_detail(
                            getattr(
                                record_a,
                                "provider_first_name",
                                None,
                            )
                            or getattr(
                                record_a,
                                "first_name",
                                None,
                            ),
                            getattr(
                                record_b,
                                "provider_first_name",
                                None,
                            )
                            or getattr(
                                record_b,
                                "first_name",
                                None,
                            ),

                            getattr(
                                record_a,
                                "provider_last_name",
                                None,
                            )
                            or getattr(
                                record_a,
                                "last_name",
                                None,
                            ),

                            getattr(
                                record_b,
                                "provider_last_name",
                                None,
                            )
                            or getattr(
                                record_b,
                                "last_name",
                                None,
            ),
        ),
            signal_type="probabilistic",
    )
)
            
            print(vars(record_a))
            print(vars(record_b))
            print("PROVIDER RECORD A:")
            print(vars(record_a))

            print("PROVIDER RECORD B:")
            print(vars(record_b))

            signals.append(
                        self._build_signal(
                            "source_trust",
                            source_score,
                            domain_weights.get("source_trust", 0.0),
                            self._source_detail(
                                record_a.source_system,
                                record_b.source_system,
                            ),
                            signal_type="probabilistic",
                        )
                    )

            # ----------------------------------------
            # Composite Signal Reinforcement
            # ----------------------------------------

            if (
                            provider_email_score >= 1.0
                            and name_score >= 0.85
                            and address_score >= 0.90
                        ):
                        match_score += 0.15

            elif (
                name_score >= 0.90
                and address_score >= 0.90
            ):
                match_score += 0.10

            elif (
                provider_email_score >= 1.0
                and name_score >= 0.80
            ):
                match_score += 0.08

            # Prevent overflow
            match_score = min(match_score, 1.0)
                        
            raw_entity_score = round(match_score * risk_multiplier,4,)
    
            
        elif normalized_domain == "PATIENT":

            signals = []

            patient_first_name_a = (
                getattr(record_a, "patient_first_name", None)
                or getattr(record_a, "first_name", None)
            )
            patient_first_name_b = (
                getattr(record_b, "patient_first_name", None)
                or getattr(record_b, "first_name", None)
            )

            patient_last_name_a = (
                getattr(record_a, "patient_last_name", None)
                or getattr(record_a, "last_name", None)
            )
            patient_last_name_b = (
                getattr(record_b, "patient_last_name", None)
                or getattr(record_b, "last_name", None)
            )

            patient_dob_a = (
                getattr(record_a, "patient_dob", None)
                or getattr(record_a, "dob", None)
            )
            patient_dob_b = (
                getattr(record_b, "patient_dob", None)
                or getattr(record_b, "dob", None)
            )

            patient_email_a = (
                getattr(record_a, "patient_email", None)
                or getattr(record_a, "email", None)
                or ""
            ).strip().lower()

            patient_email_b = (
                getattr(record_b, "patient_email", None)
                or getattr(record_b, "email", None)
                or ""
            ).strip().lower()

            patient_address_a = (
                getattr(record_a, "patient_address", None)
                or getattr(record_a, "address", None)
            )
            patient_address_b = (
                getattr(record_b, "patient_address", None)
                or getattr(record_b, "address", None)
            )

            name_score = self._name_similarity(
                patient_first_name_a,
                patient_last_name_a,
                patient_first_name_b,
                patient_last_name_b,
            )

            dob_score = self._dob_match(patient_dob_a, patient_dob_b)

            patient_email_similarity = SimilarityEngine.email_similarity(
                patient_email_a,
                patient_email_b,
            )

            patient_domain_a = (
                patient_email_a.split("@")[1]
                if "@" in patient_email_a
                else ""
            )
            patient_domain_b = (
                patient_email_b.split("@")[1]
                if "@" in patient_email_b
                else ""
            )

            patient_domain_trust_score = min(
                email_domain_trust(patient_domain_a),
                email_domain_trust(patient_domain_b),
            )

            email_score = min(
                patient_email_similarity * patient_domain_trust_score,
                1.0,
            )

            email_match_level = "DIFFERENT"
            if email_score >= 0.99:
                email_match_level = "EXACT"
            elif email_score >= 0.90:
                email_match_level = "SIMILAR"
            elif email_score >= 0.70:
                email_match_level = "FUZZY"

            address_score = SimilarityEngine.address_similarity(
                patient_address_a,
                patient_address_b,
            )

            patient_identity_score = (
                patient_id_score if patient_id_score is not None else 0.0
            )

            human_identity_component = (
                human_id_score if human_id_score is not None else 0.5
            )

            match_score = (
                patient_identity_score * 0.30
                + human_identity_component * 0.05
                + dob_score * 0.20
                + name_score * 0.20
                + address_score * 0.15
                + email_score * 0.10
                + source_score * 0.10
            )

            if (
                patient_identity_score >= 0.90
                and dob_score == 1.0
                and name_score >= 0.90
                and address_score >= 0.95
            ):
                match_score = min(max(match_score, 0.90), 0.92)

            elif (
                patient_identity_score >= 0.90
                and dob_score == 1.0
                and name_score >= 0.85
            ):
                 match_score = min(max(match_score, 0.86), 0.90)

            match_score = min(match_score, 0.92)

            signals.append(
                self._build_signal(
                    "patient_id_match",
                    patient_id_score,
                    domain_weights.get("patient_id_match", 0.0),
                    self._patient_id_detail(
                        getattr(record_a, "patient_id", None),
                        getattr(record_b, "patient_id", None),
                    ),
                    signal_type="deterministic",
                )
            )

            signals.append(
                self._build_signal(
                    "human_id_match",
                    human_id_score,
                    domain_weights.get("human_id_match", 0.0),
                    self._human_id_detail(
                        getattr(record_a, "human_id", None),
                        getattr(record_b, "human_id", None),
                    ),
                    signal_type="deterministic",
                )
            )

            signals.append(
                self._build_signal(
                    "dob_match",
                    dob_score,
                    domain_weights.get("dob_match", 0.0),
                    self._dob_detail(patient_dob_a, patient_dob_b),
                    signal_type="deterministic",
                )
            )

            signals.append(
                self._build_signal(
                    "name_similarity",
                    name_score,
                    domain_weights.get("name_similarity", 0.0),
                    self._name_detail(
                        patient_first_name_a,
                        patient_last_name_a,
                        patient_first_name_b,
                        patient_last_name_b,
                    ),
                    signal_type="probabilistic",
                )
            )

            signals.append(
                self._build_signal(
                    "email_match",
                    email_score,
                    domain_weights.get("email_match", 0.0),
                    self._email_detail(patient_email_a, patient_email_b),
                    match_level=email_match_level,
                    domain_trust=patient_domain_trust_score,
                    signal_type="probabilistic",
                )
            )

            signals.append(
                self._build_signal(
                    "address_similarity",
                    address_score,
                    domain_weights.get("address_similarity", 0.0),
                    self._address_detail(
                        patient_address_a,
                        patient_address_b,
                        address_score,
                        domain,
                        address_match_insight,
                    ),
                    signal_type="probabilistic",
                )
            )

            signals.append(
                self._build_signal(
                    "source_trust",
                    source_score,
                    domain_weights.get("source_trust", 0.0),
                    self._source_detail(
                        record_a.source_system,
                        record_b.source_system,
                    ),
                    signal_type="probabilistic",
                )
            )

            raw_entity_score = round(match_score * risk_multiplier, 4)
            
        
        signal_scores = self._domain_signal_scores(
            domain=domain,
            member_id_score=member_id_score,
            name_score=name_score,
            dob_score=dob_score,
            email_score=email_score,
            address_score=address_score,
            source_score=source_score,
            learning_score=learning_score,
            product_id_score=product_id_score,
            gtin_score=gtin_score,
            effective_lot_date_score=effective_lot_date_score,
            attribute_similarity_score=attribute_similarity_score,
            supplier_id_score=supplier_id_score,
            tax_id_score=tax_id_score,
            provider_id_score=provider_id_score,
            npi_score=npi_score,
            patient_id_score=patient_id_score,
            human_id_score=human_id_score,
            provider_email_score=provider_email_score,
            specialty_score=specialty_score,
            sku_score=sku_score,
        )
        signal_contributions = self._build_signal_contributions(
            signal_scores=signal_scores,
            signal_weights=domain_weights,
        )

        weighted_score = 0.0
        total_weight = 1.0
        

        effective_multiplier = risk_multiplier

        if domain in {"SUPPLIER", "PRODUCT", "PROVIDER"}:
            effective_multiplier = max(risk_multiplier, 0.90)

        if domain in {"SUPPLIER", "PRODUCT", "PROVIDER", "PATIENT"}:

            domain_confidence_score = round(
                match_score * effective_multiplier * 100,
                2,
            )

        else:

            weighted_score = sum(
                item["contribution"] for item in signal_contributions
            )

            total_weight = sum(domain_weights.values())

            domain_confidence_score = round(
                (weighted_score / total_weight) * risk_multiplier * 100
                if total_weight else 0,
                2,
            )

        raw_entity_score = round(match_score * risk_multiplier, 4)

        adjusted_entity_score = raw_entity_score + learning_adjustment
        adjusted_entity_score = max(
            0.0,
            min(adjusted_entity_score, 1.0),
        )

        decision_confidence_score = max(
            0,
            min(round(domain_confidence_score), 100),
        )

        automation_tier = self._automation_tier(
            decision_confidence_score,
            thresholds=automation_thresholds,
        )

        primary_signal = self._primary_signal_from_contributions(
            signal_contributions
        )

        final_recommended_action = (
            str(recommended_action).strip().upper()
            if recommended_action
            else self._recommended_action_from_tier(automation_tier)
        )

        automation_readiness_score = self._automation_readiness_score(
            decision_confidence_score=decision_confidence_score,
            composite_risk_score=composite_risk_score,
            final_recommended_action=final_recommended_action,
        )

        automation_readiness_label = self._automation_readiness_label(
            automation_readiness_score,
            thresholds=readiness_label_thresholds,
        )

        automation_policy_status = self._automation_policy_status(
            final_recommended_action
        )

        estimated_false_positive_risk = (
            self._estimated_false_positive_risk(
                decision_confidence_score=decision_confidence_score,
                composite_risk_score=composite_risk_score,
            )
        )

        signal_packets = [
        {
            "signal_name": s.get("signal_name"),
            "signal_score": round(
                float(s.get("signal_score", 0)),
                4,
            ),
            "signal_weight": s.get("signal_weight"),
            "weighted_score": round(
                float(s.get("weighted_score", 0)),
                4,
            ),
            "detail": s.get("detail") or "",
            "signal_band": s.get("signal_band"),

            "tone": self._signal_tone(
                s.get("signal_name"),
                s.get("signal_score"),
                thresholds=signal_tone_thresholds,
        ),
    }
    for s in signals
]

        summary = self._summary(
            raw_entity_score=raw_entity_score,
            adjusted_entity_score=adjusted_entity_score,
            decision_confidence_score=decision_confidence_score,
            automation_tier=automation_tier,
            automation_readiness_score=automation_readiness_score,
            final_recommended_action=final_recommended_action,
            composite_risk_score=composite_risk_score,
        )

        match_evidence_timeline = self.build_match_evidence_timeline(
            signals=signal_packets,
            decision_confidence_score=decision_confidence_score,
            automation_tier=automation_tier,
            primary_signal=primary_signal,
            composite_risk_score=composite_risk_score,
            risk_flag=risk_flag,
            recommended_action=final_recommended_action,
            address_match_insight=address_match_insight,
            triggered_rules=req.triggered_rules,
            automation_readiness_score=automation_readiness_score,
            automation_policy_status=automation_policy_status,
            primary_risk_driver=primary_risk_driver,
            composite_risk_band=composite_risk_band,
            effective_email_score=round(email_score, 4),
)
        print("========== PROVIDER DEBUG ==========")
        print("provider_id_score:", provider_id_score)
        print("npi_score:", npi_score)
        print("name_score:", name_score)
        print("provider_email_score:", provider_email_score)
        print("address_score:", address_score)
        print("specialty_score:", specialty_score)
        print("source_score:", source_score)
        print("learning_score:", learning_score)

        print("match_score:", match_score)
        print("raw_entity_score:", raw_entity_score)
        print("adjusted_entity_score:", adjusted_entity_score)
        print("decision_confidence_score:", decision_confidence_score)
        print("====================================")
        return {

            "signal_contributions": signal_contributions,
            "effective_email_score": round(email_score, 4),
            "email_match_level": SimilarityEngine.similarity_band(
                round(email_score, 4)
            ),

            "email_domain_trust": round(domain_trust_score, 4),
            "signals": signal_packets,
            "raw_entity_score": raw_entity_score,
            "match_score": round(match_score, 2),
            "learning_adjustment": round(learning_adjustment, 4),
            "risk_multiplier": round(risk_multiplier, 4),
            "adjusted_entity_score": round(adjusted_entity_score, 4),
            "decision_confidence_score": decision_confidence_score,
            "signal_weights": domain_weights,
            "automation_tier": automation_tier,
            "automation_readiness_score": automation_readiness_score,
            "automation_readiness_label": automation_readiness_label,
            "automation_policy_status": automation_policy_status,
            "final_recommended_action": final_recommended_action,
            "estimated_false_positive_risk": estimated_false_positive_risk,
            "primary_signal": primary_signal,
            "summary": summary,
            "entity_resolution_summary": summary,
            "timeline_events": match_evidence_timeline,
            "match_evidence_timeline": match_evidence_timeline,
        }

    def get_domain_signal_weights(self, domain: str) -> dict[str, float]:
        normalized_domain = (domain or "CUSTOMER").upper()
        weights = self.DOMAIN_SIGNAL_WEIGHTS.get(
            normalized_domain,
            self.DOMAIN_SIGNAL_WEIGHTS["CUSTOMER"],
        ).copy()
        total = sum(weights.values())
        if total <= 0:
            return weights
        return {key: round(value / total, 6) for key, value in weights.items()}

    def _domain_signal_scores(
        self,
        domain: str,
        member_id_score: float,
        name_score: float,
        dob_score: float,
        email_score: float,
        address_score: float,
        source_score: float,
        learning_score: float,
        product_id_score: float = 0.0,
        gtin_score: float = 0.0,
        effective_lot_date_score: float = 0.0,
        attribute_similarity_score: float = 0.0,
        supplier_id_score: float = 0.0,
        tax_id_score: float = 0.0,
        provider_id_score: float = 0.0,
        npi_score: float = 0.0,
        patient_id_score: float = 0.0,
        human_id_score: float = 0.0,
        provider_email_score: float = 0.0,
        specialty_score: float = 0.0,
        sku_score: float = 0.0,
    ) -> dict[str, float]:

        normalized_domain = (domain or "CUSTOMER").upper()

        if normalized_domain == "SUPPLIER":
            return {
                "supplier_id_match": supplier_id_score,
                "tax_id_match": tax_id_score,
                "name_similarity": name_score,
                "contact_email_match": email_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        if normalized_domain == "PRODUCT":
            return {
                "product_id_match": product_id_score,
                "gtin_match": gtin_score,
                "name_similarity": name_score,
                "sku_match": sku_score,
                "effective_lot_date_match": effective_lot_date_score,
                "attribute_similarity": attribute_similarity_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }
        
        if normalized_domain == "PROVIDER":
            return {
                "provider_id_match": provider_id_score,
                "npi_match": npi_score,
                "name_similarity": name_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
                "provider_email_match": provider_email_score,
                "specialty_similarity": specialty_score,
            }

        if normalized_domain == "PATIENT":
            return {
                "patient_id_match": patient_id_score,
                "human_id_match": human_id_score,
                "name_similarity": name_score,
                "dob_match": dob_score,
                "email_match": email_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }
        else:
            return {
                "member_id_match": member_id_score,
                "name_similarity": name_score,
                "dob_match": dob_score,
                "email_match": email_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

    def _build_signal_contributions(
        self,
        signal_scores: dict[str, float],
        signal_weights: dict[str, float],
    ) -> list[dict[str, float | str]]:
        contributions: list[dict[str, float | str]] = []

        for signal_key, score in signal_scores.items():
            safe_score = self._safe_score(score)
            weight = self._safe_score(signal_weights.get(signal_key, 0.0))
            contribution = safe_score * weight
            contributions.append(
                {
                    "signal_name": signal_key,
                    "score": round(safe_score, 4),
                    "weight": round(weight, 4),
                    "contribution": round(contribution, 4),
                    "contribution_pct": round(contribution * 100, 2),
                }
            )

        return contributions

    def _primary_signal_from_contributions(
        self,
        signal_contributions: list[dict[str, Any]],
    ) -> Optional[str]:
        if not signal_contributions:
            return None
        strongest = max(
            signal_contributions,
            key=lambda item: float(item.get("contribution") or 0.0),
        )
        return str(strongest.get("signal_name"))

    def build_match_evidence_timeline(
        self,
        signals: list[dict[str, Any]],
        decision_confidence_score: int,
        automation_tier: str,
        primary_signal: str | None,
        composite_risk_score: int | None,
        automation_readiness_score: int,
        risk_flag: str | None,
        effective_email_score: float,
        recommended_action: str | None,
        address_match_insight: str | None,
        triggered_rules: list[str] | None,
        automation_policy_status: str,
        primary_risk_driver: str | None = None,
        composite_risk_band: str | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        automation_readiness_score = float(
        automation_readiness_score or 0
    )

        decision_confidence_score = float(
        decision_confidence_score or 0
    )

        composite_risk_score = (
           float(composite_risk_score or 0)
            if composite_risk_score is not None
        else 0
)
        deterministic_signal_names = {
            "member_id_match",
            "patient_id_match",
            "human_id_match",
            "provider_id_match",
            "supplier_id_match",
            "product_id_match",
            "gtin_match",
            "sku_match",
            "effective_lot_date_match",
            "npi_match",
            "tax_id_match",
            "dob_match",
            "email_match",
            "provider_email_match",
        }

        supporting_signal_names = {
            "address_similarity",
            "name_similarity",
            "specialty_similarity",
            "source_trust",
            "attribute_similarity",
        }

        signal_display_map = {
            "supplier_id_match": "Supplier ID",
            "tax_id_match": "Tax ID",
            "provider_id_match": "Provider ID",
            "patient_id_match": "Patient ID",
            "product_id_match": "Product ID",
            "gtin_match": "GTIN",
            "npi_match": "NPI",
            "human_id_match": "Human ID",
            "sku_match": "SKU",
            "effective_lot_date_match": "Effective / Lot Date",
            "attribute_similarity": "Product Attribute Similarity",
            "member_id_match": "Member ID",
            "dob_match": "Date of Birth",
            "email_match": "Email",
            "provider_email_match": "Provider Email",
            "address_similarity": "Address Similarity",
            "name_similarity": "Name Similarity",
            "specialty_similarity": "Specialty Similarity",
            "source_trust": "Source Trust",
        }

        timeline_signals = [
            s
            for s in signals
            if s.get("signal_name") in deterministic_signal_names
            or s.get("signal_name") in supporting_signal_names
        ]

        for signal in timeline_signals:
            signal_name = signal.get("signal_name", "Signal")
            display_name = signal_display_map.get(
                signal_name,
                signal_name.replace("_", " ").title(),
            )

            signal_score = float(
                signal.get("signal_score")
                if signal.get("signal_score") is not None
                else signal.get("score") or 0
            )

            if signal_name in {"product_id_match","patient_id_match","provider_id_match",}:

                if signal_score >= 0.99:
                    title = f"{display_name} Exact Match"
                    status = "positive"
                    tone = "positive"

                elif signal_score >= 0.85:
                    title = f"{display_name} Similar"
                    status = "neutral"
                    tone = "neutral"

                else:
                    title = f"{display_name} Not Matched"
                    status = "warning"
                    tone = "warning"

            elif signal_name in deterministic_signal_names:

                if signal_score >= 1.0:
                    title = f"{display_name} Exact Match"
                    status = "positive"
                    tone = "positive"

                else:
                    title = f"{display_name} Not Matched"
                    status = "warning"
                    tone = "warning"



            elif signal_name in deterministic_signal_names:
                if signal_score >= 1.0:
                    title = f"{display_name} Exact Match"
                    status = "positive"
                    tone = "positive"
                else:
                    title = f"{display_name} Not Matched"
                    status = "warning"
                    tone = "warning"
            else:
                if signal_score >= 0.85:
                    title = f"{display_name} Strong"
                    status = "positive"
                    tone = "positive"
                elif signal_score >= 0.70:
                    title = f"{display_name} Partial"
                    status = "neutral"
                    tone = "neutral"
                else:
                    title = f"{display_name} Weak"
                    status = "warning"
                    tone = "warning"

            events.append(
                {
                    "stage": "SIGNAL",
                    "step": len(events) + 1,
                    "title": title,
                    "detail": signal.get("detail", f"{display_name} evaluated."),
                    "status": status,
                    "tone": tone,
                    "signal_name": signal_name,
                    "signal_score": signal_score,
                }
            )    
        
        if address_match_insight:
            events.append(
                {
                    "step": len(events) + 1,
                    "stage": "SIGNAL",
                    "title": "Address Evidence Reviewed",
                    "detail": address_match_insight,
                    "tone": (
                        "positive"
                        if "support" in address_match_insight.lower()
                        or "highly similar" in address_match_insight.lower()
                        else "neutral"
                    ),
                    "signal_name": "address_similarity",
                    "signal_score": 0.0,
                    "policy_rule": None,
                    "impact": "MEDIUM",
                }
            )

        if triggered_rules:
            events.append(
                {
                    "step": len(events) + 1,
                    "stage": "POLICY",
                    "title": "Policy Rules Applied",
                    "detail": f"Triggered rules: {', '.join(triggered_rules)}.",
                    "tone": "neutral",
                    "signal_name": None,
                    "signal_score": 0.0,
                    "policy_rule": ",".join(triggered_rules),
                    "impact": "HIGH",
                }
            )
            
            if composite_risk_score is not None:
                normalized_risk_flag = (risk_flag or "").upper()

            risk_detail = (
                f"Composite risk scored {composite_risk_score}/100"
                f"{f' ({composite_risk_band})' if composite_risk_band else ''}"
                f" with risk flag {risk_flag or 'UNKNOWN'}."
            )
            if primary_risk_driver:
                risk_detail += f" Primary risk driver: {primary_risk_driver}."
                
            events.append(
                {
                    "step": len(events) + 1,
                    "stage": "RISK",
                    "title": "Composite Risk Calculated",
                    "detail": risk_detail,
                    "tone": (
                        "warning"
                        if normalized_risk_flag in {
                            "HIGH", 
                            "CRITICAL", 
                            "SEVERE", 
                            "ELEVATED"
                            }
                        else "neutral"
                    ),
                    "signal_name": None,
                    "signal_score": composite_risk_score / 100,
                    "policy_rule": None,
                    "impact": "HIGH",
                }
            )

            events.append(
            {
                "step": len(events) + 1,
                "stage": "AUTOMATION",
                "title": "Automation Readiness Calculated",
                "detail": (
                    f"Automation readiness scored {automation_readiness_score}/100 "
                    f"with status {automation_policy_status}."
                ),
                "tone": (
                    "positive"
                    if automation_readiness_score >= 85
                    else "warning"
                    if automation_readiness_score < 60
                    else "neutral"
                ),
                "signal_name": primary_signal,
                "signal_score": automation_readiness_score / 100,
                "policy_rule": None,
                "impact": "HIGH",
            }
        )

            final_action = (recommended_action or "").upper()

            events.append(
            {
                "step": len(events) + 1,
                "stage": "DECISION",
                "title": "Recommended Action Produced",
                "detail": (
                    f"The engine selected {recommended_action or automation_tier} "
                    f"with decision confidence {decision_confidence_score}/100."
                ),
                "tone": (
                    "positive"
                    if final_action in {"APPROVE_MERGE", "AUTO_MERGE"}
                    else "warning"
                    if final_action in {"REJECT_MERGE", "BLOCK_MERGE"}
                    else "neutral"
                ),
                "signal_name": primary_signal,
                "signal_score": decision_confidence_score / 100,
                "policy_rule": None,
                "impact": "HIGH",
            }
        )
    
        return events

    def _resolve_weights(self, policy_config: Optional[dict[str, Any]]) -> dict[str, float]:
        domain = (policy_config or {}).get("domain", "CUSTOMER").upper()
        weights = self.get_domain_signal_weights(domain)
        if not policy_config:
            return weights

        policy_weights = policy_config.get("signal_weights") or policy_config.get("weights")
        if not isinstance(policy_weights, dict):
            return weights

        for key in weights:
            value = policy_weights.get(key)
            if value is None:
                continue
            try:
                weights[key] = max(0.0, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue

        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 6) for k, v in weights.items()}

        return weights

    def _resolve_source_trust_map(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        source_trust_map = self.default_source_trust_map.copy()
        if not policy_config:
            return source_trust_map

        policy_map = policy_config.get("source_trust_map")
        if not isinstance(policy_map, dict):
            return source_trust_map

        for key, value in policy_map.items():
            try:
                source_trust_map[str(key).upper()] = max(0.0, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue

        return source_trust_map

    def _resolve_automation_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, int]:
        thresholds = self.default_automation_thresholds.copy()
        if not policy_config:
            return thresholds

        policy_thresholds = policy_config.get("automation_thresholds") or {}
        for key in thresholds:
            value = policy_thresholds.get(key)
            if value is None:
                continue
            try:
                thresholds[key] = max(0, min(int(value), 100))
            except (TypeError, ValueError):
                continue

        return thresholds

    def _resolve_signal_tone_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        thresholds = self.default_signal_tone_thresholds.copy()
        if not policy_config:
            return thresholds

        policy_thresholds = policy_config.get("signal_tone_thresholds") or {}
        for key in thresholds:
            value = policy_thresholds.get(key)
            if value is None:
                continue
            try:
                thresholds[key] = max(0.0, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue

        return thresholds

    def _resolve_readiness_label_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, int]:
        thresholds = self.default_readiness_label_thresholds.copy()
        if not policy_config:
            return thresholds

        policy_thresholds = policy_config.get("readiness_label_thresholds") or {}
        for key in thresholds:
            value = policy_thresholds.get(key)
            if value is None:
                continue
            try:
                thresholds[key] = max(0, min(int(value), 100))
            except (TypeError, ValueError):
                continue

        return thresholds
    
    def _similarity_band(
            self, 
            score: float
        ) -> str:

        print(
            "SIMILARITY_BAND INPUT:", 
            score, 
            type(score)
        )

        score = float(score or 0)

        if score >= 0.95:
            return "EXACT"
        
        if score >= 0.80:
            return "STRONG"
        
        if score >= 0.60:
            return "FUZZY"

        return "DIFFERENT"
    

    def _signal_impact(self, score: Any) -> str:

        if not isinstance(score, (int, float)):
            return "MEDIUM"

        band = SimilarityEngine.similarity_band(score)

        if band == "EXACT":
            return "HIGH"

        if band == "SIMILAR":
            return "MEDIUM"

        if band == "FUZZY":
            return "MEDIUM"

        return "LOW"

    def _build_signal(
        self,
        name: str,
        score: float,
        weight: float,
        detail: str,
        match_level: str | None = None,
        signal_type: str = "probabilistic",
        **kwargs,
    ) -> dict[str, Any]:

        if score is None:
            safe_score = 0.5
        else:
            safe_score = self._safe_score(score)

        detail = detail or (
            f"{name.replace('_', ' ').title()} was evaluated."
        )

        weighted_score = round(
            safe_score * weight,
            4,
        )

        return {
            "signal_name": name,
            "signal_score": round(safe_score, 4),
            "signal_weight": round(weight, 4),
            "weighted_score": weighted_score,
            "detail": detail,
         "signal_band": (
            match_level
                or (
                "EXACT"
                if name in {"product_id_match","sku_match","patient_id_match","provider_id_match",
            } and safe_score >= 0.999
                else "SIMILAR"
                if name in {"product_id_match","sku_match","patient_id_match","provider_id_match",
            } and safe_score >= 0.85
                else "DIFFERENT"
                    if name in {"product_id_match", "sku_match"}
                else SimilarityEngine.similarity_band(safe_score)
    )
),

            "signal_impact": self._signal_impact(
                safe_score
            ),

            "signal_type": signal_type,

            **kwargs,
    }

    def _signal_tone(
        self,
        signal_name: str,
        score: float,
        thresholds: dict[str, float],
    ) -> str:
        if score is None:
            return "neutral"
        safe_score = float(score or 0)
        safe_score = self._safe_score(score)

        print("SIGNAL_TONE INPUT:", safe_score, type(safe_score))

        strong_positive = thresholds.get("strong_positive", 0.92)
        positive = thresholds.get("positive", 0.85)
        neutral = thresholds.get("neutral", 0.70)
        neutral_source = thresholds.get("neutral_source", 0.65)
        safe_score = float(score or 0)

        
        # Deterministic identity anchors
        if signal_name in {
            "supplier_id_match",
            "product_id_match",
            "provider_id_match",
            "patient_id_match",
            "member_id_match",
            "tax_id_match",
            "gtin_match",
            "npi_match",
            "human_id_match",
            "email_match",
            "dob_match",
        }:

            if float(safe_score or 0) >= 1.0:
                return "positive"
            
            return "warning"       

        # Email identity signal
        if signal_name in {
            "email_match",
            "contact_email_match",
        }:

            if safe_score >= strong_positive:
                return "positive"

            if safe_score >= neutral:
                return "neutral"

            return "warning"

        # Name similarity
        if signal_name == "name_similarity":
        
            if safe_score >= positive:
                return "positive"

            if safe_score >= neutral:
                return "neutral"

            return "warning"

        # Address / Attribute matching
        if signal_name in {
            "address_similarity",
            "attribute_similarity",
        }:

            if safe_score >= positive:
                return "positive"

            if safe_score >= neutral:
                return "neutral"

            return "warning"

        # Source trust
        if signal_name == "source_trust":

            if safe_score >= 0.85:
                return "positive"

            if safe_score >= neutral_source:
                return "neutral"

            return "warning"

        return "neutral"
    

    def _safe_score(self, value: Optional[float], default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return default

    def _normalize_text(self, value: Optional[str]) -> str:
        return (value or "").strip().lower()

    def _similarity(self, a: Optional[str], b: Optional[str]) -> float:

        a_norm = self._normalize_text(a)
        b_norm = self._normalize_text(b)

        if not a_norm or not b_norm:
            return 0.0

        return round(
            JaroWinkler.similarity(a_norm, b_norm),
            4,
    )

    def _name_similarity(
        self,
        first_a: Optional[str],
        last_a: Optional[str],
        first_b: Optional[str],
        last_b: Optional[str],
    ) -> float:
        first_score = self._similarity(first_a, first_b)
        last_score = self._similarity(last_a, last_b)
        return round((first_score * 0.4) + (last_score * 0.6), 4)

    def _dob_match(self, dob_a: Optional[str], dob_b: Optional[str]) -> float:
        a = self._normalize_text(dob_a)
        b = self._normalize_text(dob_b)

        if not a or not b:
            return 0.0
        return 1.0 if a == b else 0.0
    
    def _member_id_match(
        self,
        member_id_a: Optional[str],
        member_id_b: Optional[str],
    ) -> float:
        a = self._normalize_text(member_id_a)
        b = self._normalize_text(member_id_b)

        if not a or not b:
            return 0.0
        
        return 1.0 if a == b else 0.0

    def _member_id_detail(
        self,
        member_id_a: Optional[str],
        member_id_b: Optional[str],
    ) -> str:
        if not member_id_a or not member_id_b:
            return "Member ID missing on one or both records."

        if self._normalize_text(member_id_a) == self._normalize_text(member_id_b):
            return (
            f"Exact Member ID match detected ({member_id_a}). "
            f"This is a strong deterministic identity signal."
        )

        return "Member ID values differ."

    def _source_trust_score(
        self,
        source_a: Optional[str],
        source_b: Optional[str],
        source_trust_map: dict[str, float],
    ) -> float:
        trust_a = source_trust_map.get((source_a or "").upper(), 0.65)
        trust_b = source_trust_map.get((source_b or "").upper(), 0.65)

        avg_trust = (trust_a + trust_b) / 2.0
        distance_penalty = abs(trust_a - trust_b) * 0.25

        return round(max(0.0, min(avg_trust - distance_penalty, 1.0)), 4)

    def _learning_adjustment(self, override_rate_estimate: Optional[float]) -> float:
        if override_rate_estimate is None:
            return 0.0

        try:
            rate = max(0.0, min(float(override_rate_estimate), 1.0))
        except (TypeError, ValueError):
            return 0.0

        if rate >= 0.30:
            return -0.10
        if rate >= 0.15:
            return -0.06
        if rate >= 0.05:
            return -0.03
        return 0.02

    def _risk_multiplier(self, composite_risk_score: Optional[int]) -> float:
        if composite_risk_score is None:
            return 1.0

        try:
            risk = max(0, min(int(composite_risk_score), 100))
        except (TypeError, ValueError):
            return 1.0

        return round(1.0 - (risk / 100.0) * 0.45, 4)

    def _automation_tier(
        self,
        confidence_score: int,
        thresholds: dict[str, int],
    ) -> str:
        if confidence_score >= thresholds["auto_merge_ready_min"]:
            return "AUTO_MERGE_READY"
        if confidence_score >= thresholds["suggested_merge_min"]:
            return "SUGGESTED_MERGE"
        if confidence_score >= thresholds["review_advised_min"]:
            return "REVIEW_ADVISED"
        return "DO_NOT_AUTOMATE"

    def _recommended_action_from_tier(self, automation_tier: str) -> str:
        tier = (automation_tier or "").upper()

        if tier == "AUTO_MERGE_READY":
            return "AUTO_MERGE"
        if tier == "SUGGESTED_MERGE":
            return "APPROVE_MERGE"
        if tier == "REVIEW_ADVISED":
            return "REVIEW_REQUIRED"
        return "BLOCK_MERGE"

    def _automation_readiness_score(
        self,
        decision_confidence_score: int,
        composite_risk_score: Optional[int],
        final_recommended_action: str,
    ) -> int:
        risk_score = 100 - max(0, min(int(composite_risk_score or 50), 100))

        action_bonus_map = {
            "AUTO_MERGE": 100,
            "APPROVE_MERGE": 75,
            "REVIEW_REQUIRED": 45,
            "REVIEW": 45,
            "BLOCK_MERGE": 10,
            "REJECT_MERGE": 10,
        }
        action_bonus = action_bonus_map.get((final_recommended_action or "").upper(), 40)

        readiness = (
            decision_confidence_score * 0.55
            + risk_score * 0.35
            + action_bonus * 0.10
        )
        return max(0, min(round(readiness), 100))

    def _automation_readiness_label(
        self,
        readiness_score: int,
        thresholds: dict[str, int],
    ) -> str:
        

        print("READINESS INPUT:",
                readiness_score,
                type(readiness_score)
            )
        if readiness_score >= thresholds["high"]:
            return "HIGH_AUTOMATION_READINESS"
        if readiness_score >= thresholds["moderate"]:
            return "MODERATE_AUTOMATION_READINESS"
        return "LOW_AUTOMATION_READINESS"
    

    def _automation_policy_status(self, final_recommended_action: str) -> str:
        action = (final_recommended_action or "").upper()

        if action == "AUTO_MERGE":
            return "ELIGIBLE_FOR_AUTO_MERGE"
        if action == "APPROVE_MERGE":
            return "APPROVAL_READY"
        if action in {"REVIEW_REQUIRED", "REVIEW"}:
            return "MANUAL_REVIEW_REQUIRED"
        if action in {"BLOCK_MERGE", "REJECT_MERGE"}:
            return "BLOCKED_BY_POLICY"
        return "MANUAL_REVIEW_REQUIRED"

    def _estimated_false_positive_risk(
        self,
        decision_confidence_score: int,
        composite_risk_score: Optional[int],
    ) -> int:
        risk_component = max(0, min(int(composite_risk_score or 50), 100))
        confidence_inverse = 100 - max(0, min(decision_confidence_score, 100))
        blended = round((risk_component * 0.65) + (confidence_inverse * 0.35))
        return max(0, min(blended, 100))

    def _primary_signal(self, signals: list[SignalScore]) -> Optional[str]:
        if not signals:
            return None
        strongest = max(signals, key=lambda s: s.weighted_score)
        return strongest.name

    def _summary(
        self,
        raw_entity_score: float,
        adjusted_entity_score: float,
        decision_confidence_score: int,
        automation_tier: str,
        automation_readiness_score: int,
        final_recommended_action: str,
        composite_risk_score: Optional[int],
    ) -> str:
        risk_text = (
            f"{composite_risk_score}/100"
            if composite_risk_score is not None
            else "unknown"
        )
        return (
            f"Raw entity score {raw_entity_score:.2f}, adjusted score {adjusted_entity_score:.2f}, "
            f"decision confidence {decision_confidence_score}%, automation tier {automation_tier}, "
            f"automation readiness {automation_readiness_score}%, final action {final_recommended_action}, "
            f"composite risk {risk_text}."
        )

    def _name_detail(
        self,
        first_a: Optional[str],
        last_a: Optional[str],
        first_b: Optional[str],
        last_b: Optional[str],
    ) -> str:
        return (
            f"Compared name pairs '{first_a or ''} {last_a or ''}' and "
            f"'{first_b or ''} {last_b or ''}'."
        )

    def _dob_detail(
        self,
        dob_a: Optional[str],
        dob_b: Optional[str],
        domain: str = "CUSTOMER",
    ) -> str:
        normalized_domain = (domain or "CUSTOMER").upper()
        field_label = (
            "effective / lot date"
            if normalized_domain == "PRODUCT"
            else "tax ID / registration date"
            if normalized_domain == "SUPPLIER"
            else "dob"
        )

        if not dob_a or not dob_b:
            return f"{field_label} missing on one or both records."
        if self._normalize_text(dob_a) == self._normalize_text(dob_b):
            return f"{field_label} values match exactly."
        return f"{field_label} values differ."

    def _email_detail(
        self,
        email_a: Optional[str],
        email_b: Optional[str],
    ) -> str:

        if not email_a or not email_b:
            return "Email missing on one or both records."

        if self._normalize_text(email_a) == self._normalize_text(email_b):
            return "Email values match exactly."

        return "Email values partially align or differ."
    
    def _contact_email_detail(
        self,
        email_a: Optional[str],
        email_b: Optional[str],
    ) -> str:

        if not email_a or not email_b:
            return "Supplier contact email missing on one or both records."

        if self._normalize_text(email_a) == self._normalize_text(email_b):
            return "Supplier contact email values match exactly."

        return "Supplier contact email values partially align or differ."
    
    def _address_detail(
        self,
        address_a,
        address_b,
        score,
        domain="CUSTOMER",
        address_match_insight=None,
    ):

        band = self._similarity_band(score)
        

        normalized_domain = (domain or "CUSTOMER").upper()

        if not address_a or not address_b:
            return "Address or attribute value missing on one or both records."

       
        if address_match_insight:
            return address_match_insight

        field_label = (
            "Product attributes"
            if normalized_domain == "PRODUCT"
            else "Supplier addresses"
            if normalized_domain == "SUPPLIER"
            else "Addresses"
        )

        return (
        f"{field_label} evaluated using "
        f"Jaro-Winkler fuzzy matching. "
        f"Confidence score: {score:.2f} "
        f"({band})."
        )

    def _source_detail(self, source_a: Optional[str], source_b: Optional[str]) -> str:
        return (
            f"Source trust evaluated for '{source_a or 'UNKNOWN'}' and "
            f"'{source_b or 'UNKNOWN'}'."            
        )
    