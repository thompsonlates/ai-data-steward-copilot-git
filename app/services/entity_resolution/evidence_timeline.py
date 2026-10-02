from typing import Any

def build_evidence_timeline(
    signals: list[dict[str, Any]],
    decision_confidence_score: int,
    automation_tier: str,
    primary_signal: str | None,
    composite_risk_score: int | None,
    automation_readiness_score: int,
    risk_flag: str | None,
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
        "phone_match",
        "provider_id_match",
        "supplier_id_match",
        "product_id_match",
        "gtin_match",
        "sku_match",
        "effective_lot_date_match",
        "npi_match",
        "tax_id_match",
        "dob_match",
        "contact_email_match",
        "provider_email_match",
        "account_id_match",
        "banking_customer_id_match",
        "sap_business_partner_id_match",
        "routing_number_match",
        "account_number_last4_match",
        "institution_name_match",
        "account_type_match",
        "currency_match",
        "account_status_match",
        "email_match",
        # Organization
        "organization_entity_id_match",
        "organization_code_match",
        "organization_type_match",
        "parent_organization_id_match",
        "organization_status_match",

        # Location
        "location_id_match",
        "site_id_match",
        "location_code_match",
        "location_type_match",
        "parent_location_id_match",
        "organization_entity_id_match",
    }

    supporting_signal_names = {
        "address_similarity",
        "name_similarity",
        "specialty_similarity",
        "source_trust",
        "attribute_similarity",
        "item_category_similarity",
        "product_variant_similarity",
        "phone_match",
        "organization_name_similarity",
        "location_name_similarity",
    }

    signal_display_map = {
        "supplier_id_match": "Supplier ID",
        "tax_id_match": "Tax ID",
        "provider_id_match": "Provider ID",
        "patient_id_match": "Patient ID",
        "product_id_match": "Product ID",
        "gtin_match": "GTIN",
        "npi_match": "NPI",
        "phone_match": "Phone",
        "sku_match": "SKU",
        "effective_lot_date_match": "Effective / Lot Date",
        "attribute_similarity": "Product Attribute Similarity",
        "product_variant_similarity": "Product Variant / Pack Size",
        "item_category_similarity": "Item Category",
        "member_id_match": "Member ID",
        "dob_match": "Date of Birth",
        "contact_email_match": "Contact Email",
        "provider_email_match": "Provider Email",
        "address_similarity": "Address Similarity",
        "name_similarity": "Name Similarity",
        "specialty_similarity": "Specialty Similarity",
        "source_trust": "Source Trust",
        "account_id_match": "Account ID",
        "banking_customer_id_match": "Banking Customer ID",
        "sap_business_partner_id_match": "SAP Business Partner ID",
        "routing_number_match": "Routing Number",
        "account_number_last4_match": "Account Number Last 4",
        "institution_name_match": "Institution Name",
        "account_type_match": "Account Type",
        "currency_match": "Currency",
        "account_status_match": "Account Status",
        "email_match": "Email",
        # Organization
        "organization_entity_id_match": "Organization Entity ID",
        "organization_code_match": "Organization Code",
        "organization_name_similarity": "Organization Name Similarity",
        "organization_type_match": "Organization Type",
        "parent_organization_id_match": "Parent Organization ID",
        "organization_status_match": "Organization Status",

        # Location
        "location_id_match": "Location ID",
        "site_id_match": "Site ID",
        "location_code_match": "Location Code",
        "location_name_similarity": "Location Name Similarity",
        "location_type_match": "Location Type",
        "parent_location_id_match": "Parent Location ID",
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
        evidence_available = signal.get("evidence_available", True)

        signal_score = float(
            signal.get("signal_score")
            if signal.get("signal_score") is not None
            else signal.get("score") or 0
        )

        if not evidence_available:
            title = f"{display_name} Evidence Unavailable"
            status = "neutral"
            tone = "neutral"

        elif signal_name in {
            "product_id_match",
            "patient_id_match",
            "provider_id_match",
        }:
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
                organization_conflict_titles = {
                    "organization_entity_id_match": "Organization Entity ID Conflict",
                    "organization_code_match": "Organization Code Conflict",
                    "organization_type_match": "Organization Type Conflict",
                    "parent_organization_id_match": "Parent Organization IDs Differ",
                    "organization_status_match": "Organization Status Difference",
                }
                title = organization_conflict_titles.get(
                    signal_name,
                    f"{display_name} Not Matched",
                )
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

    if (
        str(primary_risk_driver or "").strip().upper()
        == "ORGANIZATION_HIERARCHY_CONFLICT"
    ):
        events.append(
            {
                "stage": "RISK",
                "step": len(events) + 1,
                "title": "Parent-Child Organization Relationship Detected",
                "detail": (
                    "One organization explicitly identifies the other as its "
                    "parent organization. These records represent separate nodes "
                    "in the organization hierarchy, not duplicate organizations."
                ),
                "status": "warning",
                "tone": "warning",
                "signal_name": "organization_hierarchy",
                "signal_score": 1.0,
            }
        )
        events.append(
            {
                "stage": "RISK",
                "step": len(events) + 1,
                "title": "Organization Hierarchy Conflict Prevents Merge",
                "detail": (
                    "A parent and child organization cannot be merged as the same "
                    "mastered entity. Merging them would collapse the organization "
                    "hierarchy and corrupt entity identity."
                ),
                "status": "warning",
                "tone": "warning",
                "signal_name": "organization_hierarchy",
                "signal_score": 1.0,
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

