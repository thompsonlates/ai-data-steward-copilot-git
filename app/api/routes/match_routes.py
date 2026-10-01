"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post("/match/explain")
async def match_explain(
    request: Request,
    req: MatchExplainRequest,
    current_user: AuthUser = Depends(require_active_product_access),
    provider: str = Query(DEFAULT_LLM_PROVIDER, pattern="^(gemini|claude)$"),
):

    try: 
        user_agent = request.headers.get("User-Agent")
        integration_source = request.headers.get("X-Integration-Source")

        logger.info(
            f"[INTEGRATION] "
            f"UserAgent={user_agent} "
            f"Source={integration_source} "
            f"Domain={req.domain} "
            f"PolicyVersion={req.policy_version} ")
        
        explanation_id = f"exp_{uuid.uuid4().hex[:12]}"

        organization_id = require_current_organization_id(
            current_user
        )

    # ---------------------------------------------------
    # Domain Entitlement Enforcement
    # ---------------------------------------------------
        requested_domain = str(
            req.domain or ""
        ).strip().upper()

        if not requested_domain:
            raise HTTPException(
                status_code=400,
                detail="domain is required",
            )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=requested_domain,
        )

        # Normalize the request once entitlement has been verified.
        req.domain = requested_domain

        model_provider, model_version = get_model_metadata(
            provider
        )

        llm = LLMService(
            provider=provider
        )

        decision_ctx = policy_engine.build_decision_context(
            req,
            organization_id=organization_id,
        )

        policy_rec = decision_ctx.get("policy_recommendation", {}) or {}
        policy_cfg = decision_ctx.get("policy_config", {}) or {}
        policy_thresholds = decision_ctx.get("policy_thresholds", {}) or {}
        policy_risk_rules = decision_ctx.get("policy_risk_rules", []) or []

        domain = decision_ctx.get("domain") or req.domain
        policy_version = decision_ctx.get("policy_version") or req.policy_version

        if not domain:
            raise HTTPException(status_code=400, detail="domain is required")

        if not policy_version:
            raise HTTPException(status_code=400, detail="policy_version is required")
        
        request_id = ensure_request_id(decision_ctx.get("request_id") or req.request_id)
        audit_packet_id = decision_ctx.get("audit_packet_id")

        domain = (domain or "CUSTOMER").upper()
        req.domain = domain
        req.policy_version = policy_version
        req.request_id = request_id
      
        record_a_id = getattr(req.record_a, "member_id", None) or ""
        record_b_id = getattr(req.record_b, "member_id", None) or ""

        record_a_source_system = getattr(req.record_a, "source_system", None) or ""
        record_b_source_system = getattr(req.record_b, "source_system", None) or ""

        address_service = AddressIntelligenceService()

        domain = (req.domain or "CUSTOMER").upper()

        address_field = {
            "PROVIDER": "provider_address",
            "SUPPLIER": "supplier_address",
            "PATIENT": "patient_address",
            "LOCATION": "location_address",
        }.get(domain, "address")

        address_a = getattr(req.record_a, address_field, None)
        address_b = getattr(req.record_b, address_field, None)

        if domain == "ORGANIZATION":
            record_a_address_intelligence = None
            record_b_address_intelligence = None
            address_match_insight = None
            address_similarity_score = 0.0
        else:
            record_a_address_intelligence = address_service.validate(
                address_a
            )
            record_b_address_intelligence = address_service.validate(
                address_b
            )

            address_match_insight = address_service.compare(
                record_a_address_intelligence,
                record_b_address_intelligence,
            )

            address_similarity_score = compute_address_similarity(
                record_a_address_intelligence.standardized_address,
                record_b_address_intelligence.standardized_address,
            )
        entity_policy_config = {
            "policy_config": policy_cfg,
            "policy_thresholds": policy_thresholds,
            "policy_risk_rules": policy_risk_rules,
            "signal_weights": policy_rec.get("signal_weights"),
            "weights": policy_rec.get("weights"),
            "source_trust_map": policy_rec.get("source_trust_map"),
            "automation_thresholds": policy_rec.get("automation_thresholds"),
            "signal_tone_thresholds": policy_rec.get("signal_tone_thresholds"),
            "readiness_label_thresholds": policy_rec.get("readiness_label_thresholds"),
        }
        address_similarity_score = locals().get("address_similarity_score", 0.0)
        address_match_insight = locals().get(
        "address_match_insight",
        "No address similarity evaluated for this domain."
    )
        policy_recommended_action = str(
        policy_rec.get("recommendation") or ""
        ).strip().upper()

        final_recommended_action = (
        policy_recommended_action
        if policy_recommended_action in VALID_DECISIONS
        else "REVIEW_REQUIRED"
    )

        policy_risk_flag = str(
        policy_rec.get("highest_risk_level") or ""
    ).strip().upper()

        response_risk_flag = (
        policy_risk_flag
        if policy_risk_flag in VALID_RISK_FLAGS
        else "MEDIUM"
    )


        entity_engine = EntityResolutionEngine()
        entity_resolution = entity_engine.score(
            req=req,
            organization_id=require_current_organization_id(
                current_user
            ),
            address_similarity_score=address_similarity_score,
            override_rate_estimate=policy_rec.get("override_rate_estimate"),
            composite_risk_score=policy_rec.get("composite_risk_score"),
            risk_flag=(
            str(policy_rec.get("highest_risk_level") or "").strip().upper()
            if str(policy_rec.get("highest_risk_level") or "").strip().upper() in VALID_RISK_FLAGS
            else "MEDIUM"
        ),
        recommended_action=(
            str(policy_rec.get("recommendation") or "").strip().upper()
            if str(policy_rec.get("recommendation") or "").strip().upper() in VALID_DECISIONS
            else "REVIEW_REQUIRED"
        ),
            address_match_insight=address_match_insight,
            composite_risk_band=policy_rec.get("composite_risk_band"),
            primary_risk_driver=policy_rec.get("primary_risk_driver"),
            policy_config=entity_policy_config,
        )
                # ---------------------------------------------------
        # Attribute Conflict Risk Evaluation
        # ---------------------------------------------------
        attribute_risk = evaluate_risk(
            domain=domain,
            record_a=req.record_a,
            record_b=req.record_b,
            signal_packets=entity_resolution.get("signals"),
            recommended_action=(
                entity_resolution.get("final_recommended_action")
                or final_recommended_action
            ),
        )

        risk_drivers = attribute_risk.get(
            "risk_drivers",
            [],
)

        attribute_risk_flag = attribute_risk.get("risk_flag", "LOW")
        attribute_risk_score = attribute_risk.get("risk_score", 0)
        attribute_primary_risk_driver = attribute_risk.get(
            "primary_risk_driver",
            "NO_MAJOR_CONFLICT",
        )


        policy_risk_flag = str(
            policy_rec.get("highest_risk_level") or ""
        ).strip().upper()

        if policy_risk_flag not in VALID_RISK_FLAGS:
            policy_risk_flag = "LOW"

        risk_rank = {
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
            "SEVERE": 4,
        }

        response_risk_flag = (
            attribute_risk_flag
            if risk_rank.get(attribute_risk_flag, 1)
            >= risk_rank.get(policy_risk_flag, 1)
            else policy_risk_flag
        )

        primary_risk_driver = (
            attribute_primary_risk_driver
            if attribute_primary_risk_driver != "NO_MAJOR_CONFLICT"
            else policy_rec.get("primary_risk_driver")
        )

        composite_risk_score = max(
            int(policy_rec.get("composite_risk_score") or 0),
            int(attribute_risk_score or 0),
        )

                # ---------------------------------------------------
        # Final Action Reconciliation
        # ---------------------------------------------------
        # Merge decisions must resolve conservatively across
        # deterministic entity evidence, attribute risk, and
        # governance policy. A less-conservative policy action
        # must never override a deterministic merge block.

        policy_recommended_action = str(
            policy_rec.get("recommendation") or ""
        ).strip().upper()

        entity_recommended_action = str(
            entity_resolution.get("final_recommended_action") or ""
        ).strip().upper()

        action_rank = {
            "AUTO_MERGE": 1,
            "APPROVE_MERGE": 2,
            "REVIEW_REQUIRED": 3,
            "REVIEW": 3,
            "BLOCK_MERGE": 4,
            "REJECT_MERGE": 4,
        }

        action_candidates = [
            action
            for action in {
                policy_recommended_action,
                entity_recommended_action,
            }
            if action in action_rank
        ]

        # HIGH/CRITICAL deterministic attribute risk must never
        # result in an automated or approved merge.
        if response_risk_flag in {
            "HIGH",
            "CRITICAL",
            "SEVERE",
        }:
            action_candidates.append(
                "BLOCK_MERGE"
            )

        final_recommended_action = (
            max(
                action_candidates,
                key=lambda action: action_rank[action],
            )
            if action_candidates
            else "REVIEW_REQUIRED"
        )

        automation_metrics = (
            entity_engine.reconcile_automation_metrics(
                decision_confidence_score=int(
                    entity_resolution.get("decision_confidence_score") or 0
                ),
                composite_risk_score=composite_risk_score,
                final_recommended_action=final_recommended_action,
                policy_config=entity_policy_config,
            )
        )

        reconciled_timeline = (
            entity_engine.build_match_evidence_timeline(
                signals=entity_resolution.get("signals") or [],
                decision_confidence_score=int(
                    entity_resolution.get(
                        "decision_confidence_score"
                    )
                    or 0
                ),
                automation_tier=entity_resolution.get(
                    "automation_tier"
                )
                or "DO_NOT_AUTOMATE",
                primary_signal=entity_resolution.get(
                    "primary_signal"
                ),
                composite_risk_score=composite_risk_score,
                automation_readiness_score=int(
                    automation_metrics.get(
                        "automation_readiness_score"
                    )
                    or 0
                ),
                risk_flag=response_risk_flag,
                effective_email_score=float(
                    entity_resolution.get(
                        "effective_email_score"
                    )
                    or 0.0
                ),


                recommended_action=final_recommended_action,
                address_match_insight=address_match_insight,
                triggered_rules=req.triggered_rules,
                automation_policy_status=automation_metrics.get(
                    "automation_policy_status"
                )
                or "MANUAL_REVIEW_REQUIRED",
                primary_risk_driver=primary_risk_driver,
                composite_risk_band=response_risk_flag,
            )
        )


        prompt = build_match_explain_prompt(
            req=req,
            organization_id=organization_id,
            learning_context=decision_ctx.get("learning_context"),
            policy_context=decision_ctx.get("policy_context"),
            policy_recommendation=decision_ctx.get(
                "policy_recommendation"
            ),
            signal_packets=decision_ctx.get("signal_packets"),
)
        try:
           ai_payload = llm.generate_explanation(
            prompt,
            organization_id=require_current_organization_id(
                current_user
            ),
        )
        except Exception as e:
            print("LLM PARSE FAILURE:")
            print(str(e))

            retry_prompt = build_retry_prompt(prompt)
            ai_payload = llm.generate_explanation(
                retry_prompt,
                organization_id=organization_id
                ),
            

            ai_payload = normalize_ai_payload(ai_payload)

            # ---------------------------------------------------
            # Governance Workflow Orchestration
            # ---------------------------------------------------


        workflow_result = None

        try:
            orchestrator = WorkflowOrchestrator()

            workflow_payload = {
                **ai_payload,
                "explanation_id": explanation_id,
                "request_id": request_id,
                "organization_id": require_current_organization_id(current_user),
                "domain": domain,
                "policy_version": policy_version,
                "record_a": req.record_a.model_dump(),
                "record_b": req.record_b.model_dump(),
                "primary_risk_driver": primary_risk_driver,
                "composite_risk_score": composite_risk_score,
                "composite_risk_band": policy_rec.get("composite_risk_band"),
                "risk_flag": response_risk_flag,
            }

            workflow_result = orchestrator.evaluate_match_explanation(
                workflow_payload
            )

            print(
                f"Governance Workflow Result: "
                f"{workflow_result}"
            )

        except Exception as workflow_error:
            print(
                "Governance workflow orchestration failed:"
            )
            print(str(workflow_error))

        # Auto-add deterministic Member ID rule
        record_a_member_id = getattr(req.record_a, "member_id", None)
        record_b_member_id = getattr(req.record_b, "member_id", None)

        if (
            domain not in {"ORGANIZATION", "LOCATION"}
            and record_a_member_id
            and record_b_member_id
            and str(record_a_member_id).strip().lower()
            == str(record_b_member_id).strip().lower()
            ):

                if req.triggered_rules is None:
                    req.triggered_rules = []

                if "MEMBER_ID_EXACT" not in req.triggered_rules:
                    req.triggered_rules.append("MEMBER_ID_EXACT")

                existing_rules = {
                    item.get("rule")
                    for item in ai_payload.get("rule_analysis", [])
                    if isinstance(item, dict)
                }

                if "MEMBER_ID_EXACT" not in existing_rules:
                    ai_payload["rule_analysis"].insert(
                        0,
                            {
                                "rule": "MEMBER_ID_EXACT",
                                "impact": "HIGH",
                                "reason": (
                                    "Exact Member ID match strongly indicates the same member."
                            ),
                        },
                    )
              
        insight_prompt = build_ai_insight_prompt(
                    domain=domain,
                    ai_decision=ai_payload["ai_decision"],
                    recommended_action=final_recommended_action,
                    confidence=entity_resolution.get("decision_confidence_score") or 0,
                    risk_flag=response_risk_flag,
                    triggered_rules=req.triggered_rules or [],
                    primary_signal=entity_resolution.get("primary_signal"),
                    composite_risk_score=(
                        entity_resolution.get("composite_risk_score")
                        or policy_rec.get("composite_risk_score")
                    ),
                    signal_contributions=entity_resolution.get("signal_contributions"),
                    primary_risk_driver=primary_risk_driver,
                )
        ai_insight = generate_text_insight(
                    llm,
                    insight_prompt,
                    organization_id=require_current_organization_id(
                        current_user
    ),
)
        print(
            "CONFIDENCE DEBUG:",
            {
                "ai_payload_confidence": ai_payload.get("confidence"),
                "decision_confidence_score": entity_resolution.get("decision_confidence_score"),
            },
)

        response_obj = MatchExplainResponse(
            explanation_id=explanation_id,
            organization_id=require_current_organization_id(current_user),
            ai_decision=ai_payload["ai_decision"],
            confidence=ai_payload["confidence"],
            risk_flag=response_risk_flag,
            risk_drivers=risk_drivers,
            match_score=entity_resolution.get("match_score"),
            explanation_summary=ai_payload["explanation_summary"],
            rule_analysis=ai_payload["rule_analysis"],
            recommended_action=final_recommended_action,
            final_recommended_action=final_recommended_action,
            model_version=model_version,
            model_provider=model_provider,
            prompt_version=DEFAULT_PROMPT_VERSION,
            feature_schema_version=DEFAULT_FEATURE_SCHEMA_VERSION,
            domain=domain,
            policy_version=policy_version,
            policy_hash=None,
            request_id=request_id,
            trace_id=request_id,
            audit_packet_id=audit_packet_id,
            composite_risk_score=composite_risk_score,
            composite_risk_band=policy_rec.get("composite_risk_band"),
            primary_risk_driver=primary_risk_driver,
            record_a_address_intelligence=record_a_address_intelligence,
            record_b_address_intelligence=record_b_address_intelligence,
            address_match_insight=address_match_insight,
            address_similarity_score=address_similarity_score,
            decision_confidence_score=entity_resolution.get("decision_confidence_score"),
            automation_tier=entity_resolution.get("automation_tier"),
            automation_readiness_score=automation_metrics.get("automation_readiness_score"),
            automation_readiness_label=automation_metrics.get("automation_readiness_label"),
            automation_policy_status=automation_metrics.get("automation_policy_status"),
            estimated_false_positive_risk=automation_metrics.get(
            "estimated_false_positive_risk"
        ),
            entity_similarity_score=entity_resolution.get(
            "entity_similarity_score",
            entity_resolution.get("match_score", 0.0),
            ),
            primary_signal=entity_resolution.get("primary_signal"),
            signal_packets=normalize_entity_resolution_signals(
            entity_resolution.get("signals")
        ),
            entity_resolution_summary=entity_resolution.get("entity_resolution_summary"),
           match_evidence_timeline=normalize_match_evidence_timeline(
                reconciled_timeline
        ),
            timeline_events=normalize_match_evidence_timeline(
                reconciled_timeline
        ),
            ai_insight=ai_insight,
            entity_resolution_signals=normalize_entity_resolution_signals(
            entity_resolution.get("signals")
        ),
            
            signal_weights=entity_resolution.get("signal_weights"),
            signal_contributions=entity_resolution.get("signal_contributions"),
            timeline_version="v2",
            workflow_ticket_created=(
            workflow_result.created
            if workflow_result
            else False
        ),

            workflow_ticket_key=(
            workflow_result.jira_key
            if workflow_result
            else None
        ),

            workflow_ticket_url=(
            workflow_result.jira_url
            if workflow_result
            else None
        ),
        )

        log_row = {
            "explanation_id": explanation_id,
            "organization_id": require_current_organization_id(current_user),
            "request_id": request_id,
            "audit_packet_id": audit_packet_id,
            "domain": domain,
            "policy_version": policy_version,
            "policy_hash": None,
            "record_a_id": record_a_id,
            "record_b_id": record_b_id,
            "record_a_source_system": record_a_source_system,
            "record_b_source_system": record_b_source_system,
            "match_score": entity_resolution.get("match_score"),
            "triggered_rules": req.triggered_rules,
            "requested_by": current_user.email,
            "context_id": req.context_id,
            "ai_decision": response_obj.ai_decision,
            "ai_confidence": response_obj.confidence,
            "risk_flag": response_obj.risk_flag,
            "recommended_action": response_obj.recommended_action,
            "final_recommended_action": response_obj.final_recommended_action,
            "automation_readiness_score": response_obj.automation_readiness_score,
            "automation_readiness_label": response_obj.automation_readiness_label,
            "automation_policy_status": response_obj.automation_policy_status,
            "estimated_false_positive_risk": response_obj.estimated_false_positive_risk,
            "composite_risk_score": response_obj.composite_risk_score,
            "composite_risk_band": response_obj.composite_risk_band,
            "primary_risk_driver": response_obj.primary_risk_driver,
            "decision_confidence_score": response_obj.decision_confidence_score,
            "automation_tier": response_obj.automation_tier,
            "primary_signal": response_obj.primary_signal,
            "steward_decision": None,
            "steward_override_reason": None,
            "steward_user": None,
            "feedback_at": None,
            "steward_override_flag": None,
            "model_provider": model_provider,
            "model_version": model_version,
            "prompt_version": DEFAULT_PROMPT_VERSION,
            "feature_schema_version": DEFAULT_FEATURE_SCHEMA_VERSION,
            "created_at": utc_now_iso(),
        }

        logger.info(
            "Logging explanation for authenticated user: %s",
            current_user.email,
        )

        bq.log_explanation(
            log_row,
            organization_id=require_current_organization_id(current_user),
        )

                # ---------------------------------------------------
        # Tenant-aware Record Search Index Upsert
        # ---------------------------------------------------
        try:
                record_search_repository.upsert_record(
                    organization_id=organization_id,
                    domain=domain,
                    record=req.record_a.model_dump(),
                    created_by=current_user.email,
                    record_origin="MANUAL_ENTRY",
                )

                record_search_repository.upsert_record(
                    organization_id=organization_id,
                    domain=domain,
                    record=req.record_b.model_dump(),
                    created_by=current_user.email,
                    record_origin="MANUAL_ENTRY",
                )

        except Exception as index_error:
            logger.exception(
                "Record search index upsert failed. "
                "organization_id=%s explanation_id=%s error=%s",
                organization_id,
                explanation_id,
                index_error,
            )

        return JSONResponse(content=jsonable_encoder(response_obj))

    except HTTPException:
        raise
    except Exception as e:
            print("========= FULL TRACEBACK =========")
            traceback.print_exc()
            print("==================================")

            raise HTTPException(
                status_code=500,
                detail=str(e)
    )


@router.post("/match/feedback", dependencies=[Depends(require_active_product_access)])
def match_feedback(req: MatchFeedbackRequest, current_user: AuthUser = Depends(require_active_product_access)):
    try:
        if not req.domain:
            raise HTTPException(status_code=400, detail="domain is required")

        if not req.policy_version:
            raise HTTPException(status_code=400, detail="policy_version is required")

        request_id = ensure_request_id(req.request_id)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        submitted_at = utc_now_iso()
        organization_id = require_current_organization_id(current_user)

        
        recommended_action = metrics.get_recommended_action(
                req.explanation_id,
                organization_id=organization_id,
            )

        if recommended_action is None:
            raise HTTPException(status_code=404, detail="explanation_id not found")

        override_flag = "Y" if req.steward_decision != recommended_action else "N"

        row = bq.log_feedback_event(
            explanation_id=req.explanation_id,
            organization_id=organization_id,
            steward_decision=req.steward_decision,
            steward_user=current_user.email,
            steward_override_flag=override_flag,
            request_id=request_id,
            decision_id=decision_id,
            domain=req.domain,
            policy_version=req.policy_version,
            override_reason_code=req.override_reason_code,
            override_reason_note=req.override_reason_note,
            submitted_at=submitted_at,
        )

        payload = MatchFeedbackResponse(
            organization_id=organization_id,
            decision_id=decision_id,
            explanation_id=req.explanation_id,
            status="RECORDED",
            override_flag=override_flag,
            feedback_event_id=row.get("feedback_id"),
            feedback_at=row.get("feedback_at"),
            recommended_action=recommended_action,
            audit_packet_id=row.get("audit_packet_id"),
            request_id=request_id,
            submitted_at=submitted_at,
        )

        return JSONResponse(content=jsonable_encoder(payload))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
