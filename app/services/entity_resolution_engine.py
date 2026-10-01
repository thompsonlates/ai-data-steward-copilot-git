from __future__ import annotations

from difflib import SequenceMatcher
from rapidfuzz.distance import JaroWinkler
from typing import Any, Optional
from app.services.entity_resolution.models import SignalScore
from app.services.similarity_engine import (SimilarityEngine,email_domain_trust,)
from app.services.entity_resolution.domain_scorers.location import score_location
from app.services.entity_resolution.domain_scorers.organization import (
    score_organization,
)
from app.services.entity_resolution.domain_scorers.patient import score_patient

from app.services.entity_resolution.domain_scorers.provider import score_provider
from app.services.entity_resolution.domain_scorers.supplier import score_supplier
from app.services.entity_resolution.domain_scorers.product import score_product
from app.services.entity_resolution.domain_scorers.customer import score_customer
from app.services.entity_resolution.domain_scorers.banking import score_banking
from app.services.entity_resolution.signal_builder import (
    build_signal,
    build_signal_contributions,
    primary_signal_from_contributions,
)
from app.services.entity_resolution.policy import (
    automation_policy_status,
    automation_readiness_label,
    automation_readiness_score,
    automation_tier,
    estimated_false_positive_risk,
    learning_adjustment,
    recommended_action_from_tier,
    resolve_automation_thresholds,
    resolve_readiness_label_thresholds,
    resolve_signal_tone_thresholds,
    resolve_source_trust_map,
    resolve_weights,
    risk_multiplier,
    signal_tone,
)

from app.services.entity_resolution.scoring_helpers import (
    dob_match,
    exact_match_score,
    member_id_match,
    name_similarity,
    normalize_phone,
    normalize_text,
    phone_score,
    safe_score,
    similarity,
    source_trust_score,
)

from app.services.entity_resolution.evidence_timeline import (
    build_evidence_timeline,
)


from app.api.schemas import MatchExplainRequest
from datetime import datetime


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
            "member_id_match": 0.15,
            "phone_match": 0.15,
            "name_similarity": 0.10,
            "dob_match": 0.15,
            "email_match": 0.25,
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
            "item_category_similarity": 0.10,
            "product_variant_similarity": 0.10,
            "source_trust": 0.05,
            "steward_learning": 0.05,
        },
        "PROVIDER": {
            "provider_id_match": 0.25,
            "npi_match": 0.25,
            "phone_match": 0.15,
            "name_similarity": 0.10,
            "provider_email_match": 0.10,
            "address_similarity": 0.05,
            "specialty_similarity": 0.05,
            "source_trust": 0.03,
            "steward_learning": 0.02,
},
       "PATIENT": {
            "patient_id_match": 0.20,
            "human_id_match": 0.10,
            "phone_match": 0.15,
            "dob_match": 0.20,
            "name_similarity": 0.20,
            "address_similarity": 0.15,
            "email_match": 0.05,
            "source_trust": 0.05,
            "steward_learning": 0.05,
        },

        "BANKING": {
            "account_id_match": 0.20,
            "customer_id_match": 0.15,
            "sap_business_partner_id_match": 0.15,
            "routing_number_match": 0.10,
            "account_number_last4_match": 0.05,
            "institution_name_similarity": 0.08,
            "account_type_match": 0.05,
            "currency_match": 0.03,
            "email_match": 0.07,
            "phone_match": 0.05,
            "source_trust": 0.04,
            "steward_learning": 0.03,
        },

        "LOCATION": {
            "location_id_match": 0.25,
            "site_id_match": 0.20,
            "location_code_match": 0.10,
            "location_name_similarity": 0.10,
            "address_similarity": 0.20,
            "parent_location_id_match": 0.05,
            "organization_entity_id_match": 0.05,
            "source_trust": 0.03,
            "steward_learning": 0.02,
        },

        "ORGANIZATION": {
            "organization_entity_id_match": 0.30,
            "organization_code_match": 0.20,
            "organization_name_similarity": 0.20,
            "organization_type_match": 0.10,
            "parent_organization_id_match": 0.05,
            "organization_status_match": 0.05,
            "source_trust": 0.05,
            "steward_learning": 0.05,
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

    def _account_id_detail(self, a, b) -> str:
        if not a or not b:
            return "Banking account ID evidence is unavailable on one or both records."

        if self._normalize_text(a) == self._normalize_text(b):
            return "Banking account IDs match exactly."

        return "Banking account IDs differ."


    def _customer_id_detail(self, a, b) -> str:
        if not a or not b:
            return "Banking customer ID evidence is unavailable on one or both records."

        if self._normalize_text(a) == self._normalize_text(b):
            return "Banking customer IDs match exactly."

        return "Banking customer IDs differ."


    def _sap_business_partner_id_detail(self, a, b) -> str:
        if not a or not b:
            return (
                "SAP Business Partner ID evidence is unavailable "
                "on one or both records."
            )

        if self._normalize_text(a) == self._normalize_text(b):
            return "SAP Business Partner IDs match exactly."

        return "SAP Business Partner IDs differ."


    def _routing_number_detail(self, a, b) -> str:
        if not a or not b:
            return "Routing number evidence is unavailable on one or both records."

        if self._normalize_text(a) == self._normalize_text(b):
            return "Routing numbers match exactly."

        return "Routing numbers differ."


    def _account_last4_detail(self, a, b) -> str:
        if not a or not b:
            return (
                "Account-number last-four evidence is unavailable "
                "on one or both records."
            )

        if self._normalize_text(a) == self._normalize_text(b):
            return (
                "Account-number last four digits match. "
                "This is supporting evidence only."
            )

        return "Account-number last four digits differ."

    

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
    
    def _normalize_phone(self, value: Optional[str]) -> str:
        return normalize_phone(value)

    def _phone_score(
        self,
        phone_a: Optional[str],
        phone_b: Optional[str],
    ) -> Optional[float]:
        return phone_score(phone_a, phone_b)


    def _phone_detail(
        self,
        phone_a: Optional[str],
        phone_b: Optional[str],
    ) -> str:
        normalized_a = self._normalize_phone(phone_a)
        normalized_b = self._normalize_phone(phone_b)

        if not normalized_a and not normalized_b:
            return "Phone evidence was unavailable for both records."

        if not normalized_a or not normalized_b:
            return "Phone evidence was unavailable for one record."

        if normalized_a == normalized_b:
            return "Phone numbers match after normalization."

        return "Phone numbers differ after normalization."

    
    def _is_valid_dob(self, value):

        if value in (None, ""):
            return False

        text = str(value).strip()

        for fmt in ("%Y-%m-%d",
                    "%m/%d/%Y",
                    "%Y/%m/%d"):
            try:
                datetime.strptime(text, fmt)
                return True
            except ValueError:
                pass

        return False
    
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
        
    def _attribute_similarity_detail(self, a, b):
        return ("Compared product descriptors, classifications, and attributes"
                "to assess overall product similarity beyond exact identifier matches."
        )   
    
    def _address_similarity_detail(self, a, b):
        return ("Addresses normalized to the same standardized location "
                "with high similarity confidence."
        )
    
    
    def _product_variant_detail(self, variant_a, variant_b) -> str:
        return (
            f"Compared product variant / pack size "
            f"'{variant_a or 'UNKNOWN'}' and "
            f"'{variant_b or 'UNKNOWN'}'."
        )
    
    def _item_category_detail(self, category_a, category_b) -> str:
        return (
            f"Compared item categories "
            f"'{category_a or 'UNKNOWN'}' and "
            f"'{category_b or 'UNKNOWN'}'."
        )

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
                "tenant-isolated entity resolution."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _record_organization_id(
        record: Any,
    ) -> str:
        if record is None:
            return ""

        if isinstance(record, dict):
            value = record.get("organization_id")
        else:
            value = getattr(
                record,
                "organization_id",
                None,
            )

        return str(value or "").strip()

    @classmethod
    def _assert_record_tenant(
        cls,
        *,
        record: Any,
        organization_id: str,
        record_name: str,
    ) -> None:
        record_organization_id = (
            cls._record_organization_id(
                record
            )
        )

        if (
            record_organization_id
            and record_organization_id
            != organization_id
        ):
            raise ValueError(
                f"{record_name} does not belong to "
                "the authenticated organization."
            )

    @staticmethod
    def _assert_policy_tenant(
        *,
        policy_config: Optional[
            dict[str, Any]
        ],
        organization_id: str,
    ) -> None:
        if not policy_config:
            return

        policy_organization_id = str(
            policy_config.get(
                "organization_id"
            )
            or ""
        ).strip()

        if (
            policy_organization_id
            and policy_organization_id
            != organization_id
        ):
            raise ValueError(
                "Policy configuration does not "
                "belong to the authenticated organization."
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
    
    def _exact_match_score(self, value_a: Any, value_b: Any) -> float:
        return exact_match_score(value_a, value_b)

    def _resolve_entity_id(self, record, domain: str) -> str:
        domain = (domain or "CUSTOMER").upper()

        fields_by_domain = {
            "CUSTOMER": ["member_id"],
            "PATIENT":  ["patient_id", "member_id"],
            "PROVIDER": ["human_id", "provider_id", "npi", "member_id", "phone_number"],
            "SUPPLIER": ["supplier_id", "vendor_id", "tax_id", "member_id"],
            "PRODUCT":  ["product_id", "gtin", "sku", "item_category", "member_id"],
            "BANKING":  ["account_id","sap_business_partner_id","banking_customer_id",],
            "LOCATION": ["location_id", "site_id", "location_code", "member_id"],
            "ORGANIZATION": ["organization_entity_id", "organization_code", "member_id"],
        }

        for field in fields_by_domain.get(domain, ["member_id"]):
            value = getattr(record, field, None)
            if value is not None and str(value).strip():
                return str(value).strip()

        return ""
            


    def score(
        self,
        req: MatchExplainRequest,
        *,
        organization_id: str,
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
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        record_a = req.record_a
        record_b = req.record_b

        self._assert_record_tenant(
            record=record_a,
            organization_id=(
                effective_organization_id
            ),
            record_name="Record A",
        )

        self._assert_record_tenant(
            record=record_b,
            organization_id=(
                effective_organization_id
            ),
            record_name="Record B",
        )

        self._assert_policy_tenant(
            policy_config=policy_config,
            organization_id=(
                effective_organization_id
            ),
        )

        provider_email_score = 0.0
        provider_email_a = None
        provider_email_b = None
        specialty_score = 0.0
        phone_score: Optional[float] = None
        
        product_variant_score = 0.0
        item_category_score = 0.0
        attribute_similarity_score = 0.0

        source_trust_map = self._resolve_source_trust_map(policy_config)
        automation_thresholds = self._resolve_automation_thresholds(policy_config)
        signal_tone_thresholds = self._resolve_signal_tone_thresholds(policy_config)
        readiness_label_thresholds = self._resolve_readiness_label_thresholds(policy_config
        )
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

        # ---------------------------------------------------------
        # Banking score defaults
        # These must exist for every domain because
        # _domain_signal_scores() receives them after scoring.
        # BANKING will overwrite them with actual values.
        # ---------------------------------------------------------
        account_id_score = 0.0
        customer_id_score = 0.0
        sap_business_partner_id_score = 0.0
        routing_number_score = 0.0
        account_number_last4_score = 0.0
        institution_name_score = 0.0
        account_type_score = 0.0
        currency_score = 0.0

        # Location score defaults
        location_id_score = 0.0
        site_id_score = 0.0
        location_code_score = 0.0
        location_name_score = 0.0
        parent_location_id_score = 0.0
        organization_entity_id_score = 0.0

        # Organization score defaults
        organization_code_score = 0.0
        organization_name_score = 0.0
        organization_type_score = 0.0
        parent_organization_id_score = 0.0
        organization_status_score = 0.0
                
        if normalized_domain == "CUSTOMER":
            customer_result = score_customer(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain=domain,
                domain_weights=domain_weights,
                member_id_score=member_id_score,
                record_a_id=record_a_id,
                record_b_id=record_b_id,
                dob_score=dob_score,
                email_score=email_score,
                email_match_level=email_match_level,
                domain_trust_score=domain_trust_score,
                address_score=address_score,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
                address_match_insight=address_match_insight,
            )

            signals = customer_result["signals"]
            match_score = customer_result["match_score"]
            raw_entity_score = customer_result["raw_entity_score"]

            member_id_score = customer_result["member_id_score"]
            phone_score = customer_result["phone_score"]
            dob_score = customer_result["dob_score"]
            email_score = customer_result["email_score"]
            address_score = customer_result["address_score"]


        elif normalized_domain == "PRODUCT":
            product_result = score_product(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain_weights=domain_weights,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
            )

            signals = product_result["signals"]
            match_score = product_result["match_score"]
            raw_entity_score = product_result["raw_entity_score"]

            product_id_score = product_result["product_id_score"]
            gtin_score = product_result["gtin_score"]
            sku_score = product_result["sku_score"]
            name_score = product_result["name_score"]
            product_variant_score = product_result["product_variant_score"]
            item_category_score = product_result["item_category_score"]
            attribute_similarity_score = product_result[
                "attribute_similarity_score"
            ]
            

        elif normalized_domain == "SUPPLIER":
            supplier_result = score_supplier(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain=domain,
                domain_weights=domain_weights,
                supplier_id_score=supplier_id_score,
                tax_id_score=tax_id_score,
                supplier_id_a=supplier_id_a,
                supplier_id_b=supplier_id_b,
                address_score=address_score,
                source_score=source_score,
                risk_multiplier=risk_multiplier,
                address_match_insight=address_match_insight,
            )

            signals = supplier_result["signals"]
            match_score = supplier_result["match_score"]
            raw_entity_score = supplier_result["raw_entity_score"]

            supplier_id_score = supplier_result["supplier_id_score"]
            tax_id_score = supplier_result["tax_id_score"]
            name_score = supplier_result["name_score"]
            email_score = supplier_result["email_score"]
            address_score = supplier_result["address_score"]
        
        elif normalized_domain == "PROVIDER":
            provider_result = score_provider(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain=domain,
                domain_weights=domain_weights,
                provider_id_score=provider_id_score,
                npi_score=npi_score,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
                address_match_insight=address_match_insight,
            )

            signals = provider_result["signals"]
            match_score = provider_result["match_score"]
            raw_entity_score = provider_result["raw_entity_score"]

            provider_id_score = provider_result["provider_id_score"]
            npi_score = provider_result["npi_score"]
            phone_score = provider_result["phone_score"]
            name_score = provider_result["name_score"]
            provider_email_score = provider_result["provider_email_score"]
            address_score = provider_result["address_score"]
            specialty_score = provider_result["specialty_score"]
        elif normalized_domain == "PATIENT":
            patient_result = score_patient(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain=domain,
                domain_weights=domain_weights,
                patient_id_score=patient_id_score,
                source_score=source_score,
                risk_multiplier=risk_multiplier,
                address_match_insight=address_match_insight,
            )

            signals = patient_result["signals"]
            match_score = patient_result["match_score"]
            raw_entity_score = patient_result["raw_entity_score"]

            patient_id_score = patient_result["patient_id_score"]
            phone_score = patient_result["phone_score"]
            dob_score = patient_result["dob_score"]
            name_score = patient_result["name_score"]
            email_score = patient_result["email_score"]
            address_score = patient_result["address_score"]

        elif normalized_domain == "BANKING":
            banking_result = score_banking(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain_weights=domain_weights,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
            )

            signals = banking_result["signals"]
            match_score = banking_result["match_score"]
            raw_entity_score = banking_result["raw_entity_score"]

            account_id_score = banking_result["account_id_score"]
            customer_id_score = banking_result["customer_id_score"]
            sap_business_partner_id_score = banking_result[
                "sap_business_partner_id_score"
            ]
            routing_number_score = banking_result[
                "routing_number_score"
            ]
            account_number_last4_score = banking_result[
                "account_number_last4_score"
            ]
            institution_name_score = banking_result[
                "institution_name_score"
            ]
            account_type_score = banking_result[
                "account_type_score"
            ]
            currency_score = banking_result["currency_score"]
            email_score = banking_result["email_score"]
            phone_score = banking_result["phone_score"]


        elif normalized_domain == "LOCATION":
            location_result = score_location(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain=domain,
                domain_weights=domain_weights,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
                address_match_insight=address_match_insight,
            )

            signals = location_result["signals"]
            match_score = location_result["match_score"]
            name_score = location_result["name_score"]
            address_score = location_result["address_score"]
            raw_entity_score = location_result["raw_entity_score"]

        elif normalized_domain == "ORGANIZATION":
            organization_result = score_organization(
                engine=self,
                record_a=record_a,
                record_b=record_b,
                domain_weights=domain_weights,
                source_score=source_score,
                learning_score=learning_score,
                risk_multiplier=risk_multiplier,
            )

            signals = organization_result["signals"]
            match_score = organization_result["match_score"]
            name_score = organization_result["name_score"]
            raw_entity_score = organization_result["raw_entity_score"]

            organization_entity_id_score = organization_result[
                "organization_entity_id_score"
            ]
            organization_code_score = organization_result[
                "organization_code_score"
            ]
            organization_name_score = organization_result[
                "organization_name_score"
            ]
            organization_type_score = organization_result[
                "organization_type_score"
            ]
            parent_organization_id_score = organization_result[
                "parent_organization_id_score"
            ]
            organization_status_score = organization_result[
                "organization_status_score"
            ]
            
        
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
            product_variant_score=product_variant_score,
            gtin_score=gtin_score,
            item_category_score=item_category_score,
            supplier_id_score=supplier_id_score,
            tax_id_score=tax_id_score,
            provider_id_score=provider_id_score,
            npi_score=npi_score,
            patient_id_score=patient_id_score,
            phone_score=phone_score,
            provider_email_score=provider_email_score,
            specialty_score=specialty_score,
            sku_score=sku_score,
            account_id_score=account_id_score,
            customer_id_score=customer_id_score,
            sap_business_partner_id_score=(
                sap_business_partner_id_score
            ),
            routing_number_score=routing_number_score,
            account_number_last4_score=(
                account_number_last4_score
            ),
            institution_name_score=institution_name_score,
            account_type_score=account_type_score,
            currency_score=currency_score,
            location_id_score=location_id_score,
            site_id_score=site_id_score,
            location_code_score=location_code_score,
            location_name_score=location_name_score,
            parent_location_id_score=parent_location_id_score,
            organization_entity_id_score=organization_entity_id_score,
            organization_code_score=organization_code_score,
            organization_name_score=organization_name_score,
            organization_type_score=organization_type_score,
            parent_organization_id_score=parent_organization_id_score,
            organization_status_score=organization_status_score,

        )
        signal_contributions = self._build_signal_contributions(
            signal_scores=signal_scores,
            signal_weights=domain_weights,
        )

        weighted_score = 0.0
        total_weight = 1.0
        

        effective_multiplier = risk_multiplier

        if domain in {"SUPPLIER", "PRODUCT", "PROVIDER", "PATIENT", "BANKING", "LOCATION", "ORGANIZATION"}:
            effective_multiplier = max(risk_multiplier, 0.90)

        if domain in {"SUPPLIER", "PRODUCT", "PROVIDER", "PATIENT", "BANKING", "LOCATION", "ORGANIZATION"}:

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
        signal_packets = [
            {
                "signal_name": s.get("signal_name"),
                "signal_score": round(float(s.get("signal_score", 0)), 4),
                "signal_weight": s.get("signal_weight"),
                "weighted_score": round(float(s.get("weighted_score", 0)), 4),
                "detail": s.get("detail") or "",
                "signal_band": s.get("signal_band"),
                "evidence_available": s.get("evidence_available", True),
                "tone": self._signal_tone(
                    s.get("signal_name"),
                    s.get("signal_score"),
                    thresholds=signal_tone_thresholds,
                ),
            }
            for s in signals
        ]

        # Risk values may be supplied by routes.py / risk_engine.py.
        # If not supplied, derive conservative risk inside this engine
        # so summary + timeline do not use stale LOW/default values.
        derived_risk_score = composite_risk_score
        derived_risk_flag = risk_flag
        derived_primary_risk_driver = primary_risk_driver
        derived_risk_band = composite_risk_band

        if derived_risk_score is None:
            derived_risk_score = 0

            high_risk_drivers = []

            for packet in signal_packets:
                signal_name = str(packet.get("signal_name") or "").lower()
                score = float(packet.get("signal_score") or 0)
                detail = str(packet.get("detail") or "").lower()

                if signal_name in {
                    "patient_id_match",
                    "human_id_match",
                    "member_id_match",
                    "npi_match",
                    "tax_id_match",
                    "provider_id_match",
                } and score == 0.0:
                    high_risk_drivers.append(signal_name)

                if signal_name == "dob_match" and (
                    "invalid" in detail
                    or "not a valid" in detail
                    or "data quality" in detail
                ):
                    high_risk_drivers.append("dob_invalid_format")

            if high_risk_drivers:
                derived_risk_score = 90
                derived_risk_flag = "HIGH"
                derived_primary_risk_driver = (
                    derived_primary_risk_driver
                    or f"{high_risk_drivers[0].upper()}_CONFLICT"
                )
                derived_risk_band = "HIGH"
            else:
                derived_risk_score = 12
                derived_risk_flag = derived_risk_flag or "LOW"
                derived_primary_risk_driver = (
                    derived_primary_risk_driver or "NO_MAJOR_CONFLICT"
                )
                derived_risk_band = derived_risk_band or "LOW"

        composite_risk_score = int(derived_risk_score)
        risk_flag = derived_risk_flag or "LOW"
        primary_risk_driver = derived_primary_risk_driver
        composite_risk_band = derived_risk_band or risk_flag


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
            "evidence_available": s.get("evidence_available", True),

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
            domain=domain,
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

        return {
            "organization_id": (
                effective_organization_id
            ),
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
        product_variant_score: float = 0.0,
        gtin_score: float = 0.0,
        item_category_score: float = 0.0,
        supplier_id_score: float = 0.0,
        tax_id_score: float = 0.0,
        provider_id_score: float = 0.0,
        npi_score: float = 0.0,
        patient_id_score: float = 0.0,
        phone_score: float = 0.0,
        provider_email_score: float = 0.0,
        specialty_score: float = 0.0,
        sku_score: float = 0.0,
        account_id_score: float = 0.0,
        customer_id_score: float = 0.0,
        sap_business_partner_id_score: float = 0.0,
        routing_number_score: float = 0.0,
        account_number_last4_score: float = 0.0,
        institution_name_score: float = 0.0,
        account_type_score: float = 0.0,
        currency_score: float = 0.0,
        location_id_score: float = 0.0,
        site_id_score: float = 0.0,
        location_code_score: float = 0.0,
        location_name_score: float = 0.0,
        parent_location_id_score: float = 0.0,
        organization_entity_id_score: float | None = 0.0,
        organization_code_score: float = 0.0,
        organization_name_score: float = 0.0,
        organization_type_score: float = 0.0,
        parent_organization_id_score: float = 0.0,
        organization_status_score: float = 0.0,
    )   -> dict[str, float | None]:

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

        elif normalized_domain == "PRODUCT":
            return {
                "product_id_match": product_id_score,
                "gtin_match": gtin_score,
                "name_similarity": name_score,
                "sku_match": sku_score,
                "item_category_similarity": item_category_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        elif normalized_domain == "PROVIDER":
            return {
                "provider_id_match": provider_id_score,
                "npi_match": npi_score,
                "phone_match": phone_score,
                "name_similarity": name_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
                "provider_email_match": provider_email_score,
                "specialty_similarity": specialty_score,
            }

        elif normalized_domain == "PATIENT":
            return {
                "patient_id_match": patient_id_score,
                "phone_match": phone_score,
                "name_similarity": name_score,
                "dob_match": dob_score,
                "email_match": email_score,
                "address_similarity": address_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        elif normalized_domain == "BANKING":
            return {
                "account_id_match": account_id_score,
                "customer_id_match": customer_id_score,
                "sap_business_partner_id_match": sap_business_partner_id_score,
                "routing_number_match": routing_number_score,
                "account_number_last4_match": account_number_last4_score,
                "institution_name_similarity": institution_name_score,
                "account_type_match": account_type_score,
                "currency_match": currency_score,
                "email_match": email_score,
                "phone_match": phone_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        elif normalized_domain == "LOCATION":
            return {
                "location_id_match": location_id_score,
                "site_id_match": site_id_score,
                "location_code_match": location_code_score,
                "location_name_similarity": location_name_score,
                "address_similarity": address_score,
                "parent_location_id_match": parent_location_id_score,
                "organization_entity_id_match": organization_entity_id_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        elif normalized_domain == "ORGANIZATION":
            return {
                "organization_entity_id_match": organization_entity_id_score,
                "organization_code_match": organization_code_score,
                "organization_name_similarity": organization_name_score,
                "organization_type_match": organization_type_score,
                "parent_organization_id_match": parent_organization_id_score,
                "organization_status_match": organization_status_score,
                "source_trust": source_score,
                "steward_learning": learning_score,
            }

        return {
            "member_id_match": member_id_score,
            "phone_match": phone_score,
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
            return build_signal_contributions(
                signal_scores=signal_scores,
                signal_weights=signal_weights,
                safe_score_fn=self._safe_score,
            )

    def _primary_signal_from_contributions(
        self,
        signal_contributions: list[dict[str, Any]],
    ) -> Optional[str]:
        return primary_signal_from_contributions(
            signal_contributions
        )

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
            return build_evidence_timeline(
                signals=signals,
                decision_confidence_score=decision_confidence_score,
                automation_tier=automation_tier,
                primary_signal=primary_signal,
                composite_risk_score=composite_risk_score,
                automation_readiness_score=automation_readiness_score,
                risk_flag=risk_flag,
                effective_email_score=effective_email_score,
                recommended_action=recommended_action,
                address_match_insight=address_match_insight,
                triggered_rules=triggered_rules,
                automation_policy_status=automation_policy_status,
                primary_risk_driver=primary_risk_driver,
                composite_risk_band=composite_risk_band,
            )

    def _resolve_weights(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        domain = (policy_config or {}).get("domain", "CUSTOMER").upper()
        domain_weights = self.get_domain_signal_weights(domain)
        return resolve_weights(policy_config, domain_weights)

    def _resolve_source_trust_map(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        return resolve_source_trust_map(
            policy_config,
            self.default_source_trust_map,
        )

    def _resolve_automation_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, int]:
        return resolve_automation_thresholds(
            policy_config,
            self.default_automation_thresholds,
        )

    def _resolve_signal_tone_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        return resolve_signal_tone_thresholds(
            policy_config,
            self.default_signal_tone_thresholds,
        )

    def _resolve_readiness_label_thresholds(
        self,
        policy_config: Optional[dict[str, Any]],
    ) -> dict[str, int]:
        return resolve_readiness_label_thresholds(
            policy_config,
            self.default_readiness_label_thresholds,
        )

    def _similarity_band(
            self, 
            score: float
        ) -> str:


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
            score: float | None,
            weight: float,
            detail: str,
            match_level: str | None = None,
            signal_type: str = "probabilistic",
            **kwargs,
        ) -> dict[str, Any]:
            return build_signal(
                name=name,
                score=score,
                weight=weight,
                detail=detail,
                safe_score_fn=self._safe_score,
                signal_impact_fn=self._signal_impact,
                match_level=match_level,
                signal_type=signal_type,
                **kwargs,
            )
    def _signal_tone(
        self,
        signal_name: str,
        score: float,
        thresholds: dict[str, float],
    ) -> str:
        return signal_tone(
            signal_name,
            score,
            thresholds,
        )

    def _safe_score(
        self,
        value: Optional[float],
        default: float = 0.0,
    ) -> float:
        return safe_score(value, default)

    def _normalize_text(self, value: Optional[str]) -> str:
        return normalize_text(value)

    def _similarity(
        self,
        a: Optional[str],
        b: Optional[str],
    ) -> float:
        return similarity(a, b)

    def _name_similarity(
        self,
        first_a: Optional[str],
        last_a: Optional[str],
        first_b: Optional[str],
        last_b: Optional[str],
    ) -> float:
        return name_similarity(first_a, last_a, first_b, last_b)

    def _dob_match(
        self,
        dob_a: Optional[str],
        dob_b: Optional[str],
    ) -> float:
        return dob_match(dob_a, dob_b)

    def _member_id_match(
        self,
        member_id_a: Optional[str],
        member_id_b: Optional[str],
    ) -> float:
        return member_id_match(member_id_a, member_id_b)

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
        return source_trust_score(
            source_a,
            source_b,
            source_trust_map,
        )

    def _learning_adjustment(
        self,
        override_rate_estimate: Optional[float],
    ) -> float:
        return learning_adjustment(override_rate_estimate)

    def _risk_multiplier(
        self,
        composite_risk_score: Optional[int],
    ) -> float:
        return risk_multiplier(composite_risk_score)

    def _automation_tier(
        self,
        confidence_score: int,
        thresholds: dict[str, int],
    ) -> str:
        return automation_tier(
            confidence_score,
            thresholds,
        )

    def _recommended_action_from_tier(
        self,
        automation_tier_value: str,
    ) -> str:
        return recommended_action_from_tier(
            automation_tier_value
        )

    def reconcile_automation_metrics(
        self,
        *,
        decision_confidence_score: int,
        composite_risk_score: int,
        final_recommended_action: str,
        policy_config: Optional[dict] = None,
    ) -> dict:
        """
        Recompute automation governance metrics after the route has
        reconciled deterministic evidence, attribute risk, and policy.

        This keeps readiness, policy status, and false-positive risk
        aligned with the authoritative final action.
        """
        readiness_label_thresholds = (
            self._resolve_readiness_label_thresholds(
                policy_config
            )
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

        return {
            "automation_readiness_score": automation_readiness_score,
            "automation_readiness_label": automation_readiness_label,
            "automation_policy_status": automation_policy_status,
            "estimated_false_positive_risk": estimated_false_positive_risk,
        }

    def _automation_readiness_score(
        self,
        decision_confidence_score: int,
        composite_risk_score: Optional[int],
        final_recommended_action: str,
    ) -> int:
        return automation_readiness_score(
            decision_confidence_score,
            composite_risk_score,
            final_recommended_action,
        )

    def _automation_readiness_label(
        self,
        readiness_score: int,
        thresholds: dict[str, int],
    ) -> str:
        return automation_readiness_label(
            readiness_score,
            thresholds,
        )

    def _automation_policy_status(
        self,
        final_recommended_action: str,
    ) -> str:
        return automation_policy_status(
            final_recommended_action
        )

    def _estimated_false_positive_risk(
        self,
        decision_confidence_score: int,
        composite_risk_score: Optional[int],
    ) -> int:
        return estimated_false_positive_risk(
            decision_confidence_score,
            composite_risk_score,
        )

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
        domain: str = "CUSTOMER",
    ) -> str:
        
        normalized_domain = (domain or "CUSTOMER").upper()

        if normalized_domain == "SUPPLIER":
            return (
                "Supplier identity evidence strongly supports this match. "
                "Current governance policy requires steward review before automated merge."
            )

        elif normalized_domain == "PRODUCT":
            return (
                "Product identifiers, classifications, and descriptive attributes "
                "were evaluated to determine merge suitability."
            )

        elif normalized_domain == "PROVIDER":
            return (
                "Provider identity evidence strongly supports this match. "
                "Governance policy requires steward confirmation before automation."
            )

        elif normalized_domain == "PATIENT":
            return (
                "Patient identity evidence supports this match. "
                "Governance policy requires steward validation before automated merge."
            )

        elif normalized_domain == "CUSTOMER":
            return (
                "Customer identity evidence supports this match. "
                "Governance policy requires steward validation before automated merge."
            )

        elif normalized_domain == "BANKING":
            return (
                "Banking account and customer identity evidence "
                "were evaluated using account identifiers, SAP Business "
                "Partner evidence, routing information, institution context, "
                "and supporting contact signals. Governance policy requires "
                "steward validation before automated consolidation."
    )

            
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

