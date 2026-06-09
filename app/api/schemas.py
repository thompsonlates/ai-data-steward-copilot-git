from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# -------------------------------------------------------------------
# Flexible primitive aliases
# -------------------------------------------------------------------
# These are intentionally loosened from Literal[...] to str so the app
# can accept API-driven / config-driven / Snowflake-driven values.
# Keep stricter Literal usage only where it helps internal UI/state logic.
# -------------------------------------------------------------------

DomainType = str
DecisionType = str
RiskFlag = str
ImpactLevel = str
AddressValidationStatus = str
PostalMatchLevel = str
OverrideReasonCode = str


# -------------------------------------------------------------------
# Base reusable models
# -------------------------------------------------------------------

class FlexibleBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MemberRecord(FlexibleBaseModel):
    member_id: Optional[str] = Field(
    None,
    description="Unique member or patient identifier",
)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    source_system: Optional[str] = None
    source_id: Optional[str] = None
    record_version: Optional[str] = None
    record_hash: Optional[str] = None
    patient_id: Optional[str] = None
    provider_id: Optional[str] = None
    npi: Optional[str] = None
    supplier_id: Optional[str] = None
    vendor_id: Optional[str] = None
    tax_id: Optional[str] = None
    product_id: Optional[str] = None
    gtin: Optional[str] = None
    sku: Optional[str] = None
    upc: Optional[str] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None
    uom: Optional[str] = None
    pack_size: Optional[str] = None


# -------------------------------------------------------------------
# New config-driven models for Snowflake / externalized execution
# -------------------------------------------------------------------

class SourceObjectConfig(BaseModel):
    source_database: Optional[str] = Field(None, description="Source database name")
    source_schema: Optional[str] = Field(None, description="Source schema name")
    source_table: str = Field(..., description="Source table name")
    source_pk_column: str = Field(..., description="Primary key column in the source table")


class ColumnMappingConfig(BaseModel):
    identifier_column: Optional[str] = Field(None, description="Identifier column")
    name_column: Optional[str] = Field(None, description="Full name or primary name column")
    first_name_column: Optional[str] = Field(None, description="First name column")
    last_name_column: Optional[str] = Field(None, description="Last name column")
    email_column: Optional[str] = Field(None, description="Email column")
    address_column: Optional[str] = Field(None, description="Address column")
    phone_column: Optional[str] = Field(None, description="Phone column")
    dob_column: Optional[str] = Field(None, description="Date of birth column")
    source_system_column: Optional[str] = Field(None, description="Source system column")
    additional_columns: List[str] = Field(
        default_factory=list,
        description="Additional attributes to include in analysis",
    )


class PolicyConfigInput(BaseModel):
    domain: DomainType = Field(..., description="Business domain")
    policy_version: Optional[str] = Field(None, description="Policy version identifier")
    auto_merge_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Threshold for auto merge posture",
    )
    review_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Threshold for review posture",
    )
    block_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Threshold for block posture",
    )


class MatchAnalysisRunRequest(BaseModel):
    source_object: SourceObjectConfig
    column_mapping: ColumnMappingConfig
    policy: PolicyConfigInput
    requested_by: Optional[str] = Field(None, description="User or system requesting the run")
    request_id: Optional[str] = Field(None, description="Request correlation ID")
    context_id: Optional[str] = Field(None, description="Optional batch / job / workflow context")


# -------------------------------------------------------------------
# Explainability / timeline models
# -------------------------------------------------------------------

class SignalExplanation(BaseModel):
    signal_name: str
    signal_score: float
    signal_weight: float
    weighted_score: float

    detail: str
    tone: str  # positive | neutral | warning

    signal_band: Optional[str] = None
    raw_score: Optional[float] = None
    normalized_score: Optional[float] = None
    threshold_used: Optional[float] = None

    signal_rank: Optional[int] = None
    policy_adjusted: Optional[bool] = None

    supporting_attributes: Optional[List[str]] = None

class TimelineEvent(BaseModel):
    title: str
    detail: str
    tone: str

    stage: Optional[str] = None
    event_type: Optional[str] = None
    impact: Optional[str] = None

class LearningTimelineItem(BaseModel):
    explanation_id: str
    ai_decision: str
    recommended_action: str
    steward_decision: str
    steward_override_flag: str
    override_reason: str
    steward_user: str
    feedback_at: str


class MatchEvidenceTimelineEvent(BaseModel):
    step: int
    stage: str

    title: str
    detail: str
    tone: str

    signal_name: Optional[str] = None
    signal_score: Optional[float] = None
    signal_band: Optional[str] = None
    signal_weight: Optional[float] = None
    weighted_score: Optional[float] = None
    signal_rank: Optional[int] = None

    policy_rule: Optional[str] = None
    impact: Optional[str] = None

    created_at: Optional[str] = None


class RuleAnalysis(BaseModel):
    rule: str = Field(..., description="Rule identifier, such as DOB_EXACT")
    impact: ImpactLevel = Field(..., description="Impact level")
    reason: str = Field(
        ...,
        description="Plain-language explanation of why this rule matters",
    )


class AddressIntelligenceResult(BaseModel):
    raw_address: Optional[str] = None
    standardized_address: Optional[str] = None

    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None

    validation_status: AddressValidationStatus = Field(
        ...,
        description="Address validation status",
    )
    deliverable_flag: Optional[bool] = Field(
        None,
        description="Whether the address appears deliverable",
    )
    address_confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Confidence score for address quality and structure",
    )
    postal_match_level: Optional[PostalMatchLevel] = Field(
        None,
        description="Postal match relationship when comparing addresses",
    )
    findings: List[str] = Field(
        default_factory=list,
        description="Address quality findings and standardization notes",
    )


# -------------------------------------------------------------------
# Core match request/response models
# -------------------------------------------------------------------

class MatchExplainRequest(BaseModel):
    # Keep current routes compatible
    record_a: MemberRecord = Field(..., description="First record")
    record_b: MemberRecord = Field(..., description="Second record")
    match_score: Optional[float] = Field(
    None,
    ge=0,
    le=1,
    description="Match confidence score between 0 and 1",
    )
    triggered_rules: List[str] = Field(
    default_factory=list,
    description="List of match rules triggered by the matching engine",
    )
    
    requested_by: Optional[str] = Field(None,description="Identifier of requesting steward/system",
)

    domain: Optional[DomainType] = Field(None, description="Business domain")
    policy_version: Optional[str] = Field(
        None,
        description="Policy version used for this decision",
    )
    request_id: Optional[str] = Field(
        None,
        description="Client or server generated request ID",
    )
    context_id: Optional[str] = Field(
        None,
        description="Optional grouping/case/work item ID",
    )


class MatchExplainResponse(BaseModel):
    explanation_id: str = Field(..., description="Unique ID for this explanation event")
    ai_decision: DecisionType = Field(..., description="AI decision")
    confidence: float = Field(..., ge=0, le=1, description="Model confidence from 0 to 1")
    risk_flag: RiskFlag = Field(..., description="Risk flag")
    explanation_summary: str = Field(..., description="Short steward-friendly summary")
    rule_analysis: List[RuleAnalysis] = Field(..., description="Per-rule reasoning")
    recommended_action: DecisionType = Field(
        ...,
        description="AI or intermediate recommendation",
    )
    model_version: str = Field(..., description="Model version used")
    workflow_ticket_created: Optional[bool] = None
    workflow_ticket_key: Optional[str] = None
    workflow_ticket_url: Optional[str] = None
    ai_insight: Optional[str] = None

    model_provider: Optional[str] = Field(
        None,
        description="LLM provider, such as Anthropic or Vertex",
    )
    prompt_version: Optional[str] = Field(
        None,
        description="Prompt template version",
    )
    feature_schema_version: Optional[str] = Field(
        None,
        description="Feature schema version",
    )
    match_score: float | None = None
    entity_similarity_score: float | None = None
    signal_explanations: Optional[List[SignalExplanation]] = Field(
    default_factory=list,
    description="Legacy signal explanation payload for backward compatibility",
)

    composite_risk_score: Optional[int] = Field(
        None,
        description="Composite risk score from 0 to 100",
    )
    composite_risk_band: Optional[str] = Field(
        None,
        description="Composite risk band",
    )
    primary_risk_driver: Optional[str] = Field(
        None,
        description="Primary risk rule driving the recommendation",
    )

    decision_confidence_score: Optional[int] = Field(
        None,
        description="Entity resolution decision confidence from 0 to 100",
    )
    automation_tier: Optional[str] = Field(
        None,
        description="Automation recommendation tier",
    )
    automation_readiness_score: Optional[int] = Field(
        None,
        description="Policy-adjusted automation readiness score from 0 to 100",
    )
    automation_readiness_label: Optional[str] = Field(
        None,
        description="Automation readiness label",
    )
    automation_policy_status: Optional[str] = Field(
        None,
        description="Automation policy status",
    )
    final_recommended_action: Optional[DecisionType] = Field(
        None,
        description="Final policy-governed recommendation after combining AI, policy, and risk",
    )
    estimated_false_positive_risk: Optional[int] = Field(
        None,
        description="Estimated false positive risk from 0 to 100",
    )

    primary_signal: Optional[str] = Field(
        None,
        description="Most influential matching signal",
    )
    entity_resolution_signals: Optional[List[SignalExplanation]] = Field(
        None,
        description="Signal-level entity resolution explanation packet",
    )
    entity_resolution_summary: Optional[str] = Field(
        None,
        description="Summary of the entity resolution score and automation posture",
    )
    match_evidence_timeline: Optional[List[MatchEvidenceTimelineEvent]] = Field(
        default_factory=list,
        description="Backend-generated timeline explaining why the AI believes the records match or do not match",
    )

    timeline_events: Optional[List[TimelineEvent]] = Field(
    default_factory=list,
    description="Frontend-oriented steward reasoning timeline for lightweight UI rendering",
)

    domain: Optional[DomainType] = Field(None, description="Business domain")
    policy_version: Optional[str] = Field(None, description="Policy version used")
    policy_hash: Optional[str] = Field(None,description="Immutable hash of effective policy",
    )

    request_id: Optional[str] = Field(None, description="Request correlation ID")
    trace_id: Optional[str] = Field(None, description="Tracing correlation ID")
    audit_packet_id: Optional[str] = Field(None,description="Audit or evidence packet identifier",
    )

    record_a_address_intelligence: Optional[AddressIntelligenceResult] = Field(
        None,
        description="Address intelligence results for record A",
    )
    record_b_address_intelligence: Optional[AddressIntelligenceResult] = Field(
        None,
        description="Address intelligence results for record B",
    )
    address_match_insight: Optional[str] = Field(
        None,
        description="Short summary of whether addresses support or weaken the match",
    )
    address_similarity_score: Optional[float] = Field(
        None,
        description="Similarity score between the two addresses",
    )
    email_similarity_score: Optional[float] = Field(
    None,
    description="Similarity score between email values",
    )

    email_match_level: Optional[str] = Field(
        None,
        description="Email relationship classification such as EXACT, SIMILAR, FUZZY, DIFFERENT",
    )

    email_domain_trust: Optional[float] = Field(
        None,
        description="Trust score of compared email domains",
    )

    name_similarity_score: Optional[float] = Field(
        None,
        description="Similarity score between names",
    )

    dob_match: Optional[bool] = Field(
        None,
        description="Whether DOB values match",
    )

    email_match: Optional[bool] = Field(
        None,
        description="Whether email values match",
    )

    source_system_trust_score: Optional[float] = Field(
        None,
        description="Combined source system trust score",
    )

    override_rate_estimate: Optional[float] = Field(
        None,
        description="Estimated override likelihood",
    )
    
    signal_weights: Optional[Dict[str, float]] = None
    signal_contributions: Optional[List[Dict[str, Any]]] = None

  
# -------------------------------------------------------------------
# Policy config models
# -------------------------------------------------------------------

class PolicyConfigModel(BaseModel):
    policy_id: Optional[str] = Field(
        None,
        description="Unique policy configuration ID",
    )
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Policy version identifier")
    policy_name: Optional[str] = Field(None, description="Friendly policy name")
    policy_description: Optional[str] = Field(None, description="Policy description")
    active_flag: str = Field(
    default='Y',
    description="Whether this risk rule is active",
    )
    effective_from: Optional[str] = Field(
        None,
        description="Policy effective start timestamp",
    )
    effective_to: Optional[str] = Field(
        None,
        description="Policy effective end timestamp",
    )
    default_decision_mode: str = Field(..., description="Default decision mode")
    review_required_flag: str = Field(
        ...,
        description="Whether review is required",
    )
    allow_auto_merge_flag: str = Field(
        ...,
        description="Whether auto merge is allowed",
    )


class PolicyThresholdModel(BaseModel):
    threshold_id: Optional[str] = Field(
        None,
        description="Unique threshold configuration ID",
    )
    policy_id: Optional[str] = Field(None, description="Parent policy ID")
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Policy version identifier")
    min_review_score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Minimum score for review",
    )
    min_approve_merge_score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Minimum score for approve merge",
    )
    min_auto_merge_score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Minimum score for auto merge",
    )
    max_auto_merge_override_rate: float = Field(
        ...,
        ge=0,
        le=1,
        description="Maximum override rate tolerated for auto merge",
    )
    max_review_override_rate: float = Field(
        ...,
        ge=0,
        le=1,
        description="Maximum override rate tolerated for review posture",
    )
    high_risk_score_cutoff: float = Field(
        ...,
        ge=0,
        le=1,
        description="Cutoff for high risk score band",
    )
    medium_risk_score_cutoff: float = Field(
        ...,
        ge=0,
        le=1,
        description="Cutoff for medium risk score band",
    )
    require_exact_dob_flag: str = Field(
        ...,
        description="Whether exact DOB is required",
    )
    require_email_or_address_flag: str = Field(
        ...,
        description="Whether email or address support is required",
    )
    require_manual_review_on_conflict_flag: str = Field(
        ...,
        description="Whether conflicting evidence requires manual review",
    )
    active_flag: str = Field(
        default='Y',
        description="Whether this risk rule is active",
    )
    effective_from: Optional[str] = Field(
        None,
        description="Threshold effective start timestamp",
    )
    effective_to: Optional[str] = Field(
        None,
        description="Threshold effective end timestamp",
    )


class PolicyRiskRuleModel(BaseModel):
    risk_rule_id: Optional[str] = Field(None, description="Unique risk rule ID")
    policy_id: Optional[str] = Field(None, description="Parent policy ID")
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Policy version identifier")
    triggered_rule: str = Field(
        ...,
        description="Match rule that triggers this policy rule",
    )
    override_reason_code: Optional[OverrideReasonCode] = Field(
        None,
        description="Structured override reason code associated with the risk rule",
    )
    risk_weight: Optional[float] = Field(
        None,
        description="Relative policy risk weight",
    )
    risk_level: Optional[str] = Field(
        None,
        description="Risk level such as LOW, MEDIUM, HIGH, or CRITICAL",
    )
    recommended_action: Optional[str] = Field(
        None,
        description="Policy recommendation",
    )
    steward_learning_enabled_flag: Optional[str] = Field(
        None,
        description="Whether steward learning is enabled for this rule",
    )
    active_flag: str = Field(
        default='Y',
        description="Whether this risk rule is active",
    )
    effective_from: Optional[str] = Field(
        None,
        description="Risk rule effective start timestamp",
    )
    effective_to: Optional[str] = Field(
        None,
        description="Risk rule effective end timestamp",
    )
    notes: Optional[str] = Field(None, description="Additional rule notes")


class PolicyConfigResponse(BaseModel):
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Resolved policy version")
    config: Optional[PolicyConfigModel] = Field(
        None,
        description="Resolved policy config row",
    )
    thresholds: Optional[PolicyThresholdModel] = Field(
        None,
        description="Resolved threshold config row",
    )
    risk_rules: List[PolicyRiskRuleModel] = Field(
        default_factory=list,
        description="Resolved risk rules for the policy version",
    )
    generated_at: Optional[str] = Field(
        None,
        description="Response generation timestamp",
    )


class PolicyDraftRequest(BaseModel):
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Draft policy version identifier")

    policy_id: Optional[str] = Field(None, description="Optional existing policy ID")
    threshold_id: Optional[str] = Field(
        None,
        description="Optional existing threshold ID",
    )

    policy_name: Optional[str] = Field(None, description="Friendly policy name")
    policy_description: Optional[str] = Field(None, description="Policy description")

    default_decision_mode: Optional[str] = Field(
        None,
        description="Default decision mode",
    )
    review_required_flag: Optional[str] = Field(
        None,
        description="Whether review is required",
    )
    allow_auto_merge_flag: Optional[str] = Field(
        None,
        description="Whether auto merge is allowed",
    )

    min_review_score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Minimum score for review",
    )
    min_approve_merge_score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Minimum score for approve merge",
    )
    min_auto_merge_score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Minimum score for auto merge",
    )
    max_auto_merge_override_rate: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Maximum override rate tolerated for auto merge",
    )
    max_review_override_rate: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Maximum override rate tolerated for review posture",
    )
    high_risk_score_cutoff: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Cutoff for high risk band",
    )
    medium_risk_score_cutoff: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Cutoff for medium risk band",
    )
    require_exact_dob_flag: Optional[str] = Field(
        None,
        description="Whether exact DOB is required",
    )
    require_email_or_address_flag: Optional[str] = Field(
        None,
        description="Whether email or address support is required",
    )
    require_manual_review_on_conflict_flag: Optional[str] = Field(
        None,
        description="Whether conflicting evidence requires manual review",
    )

    effective_from: Optional[str] = Field(
        None,
        description="Draft effective start timestamp",
    )
    effective_to: Optional[str] = Field(
        None,
        description="Draft effective end timestamp",
    )
    updated_by: Optional[str] = Field(None, description="User saving the draft")


class PolicyDraftResponse(BaseModel):
    status: str = Field(..., description="Draft save status")
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Draft policy version")
    updated_by: Optional[str] = Field(None, description="User who saved the draft")
    saved_at: Optional[str] = Field(None, description="Draft save timestamp")
    config: PolicyConfigModel = Field(..., description="Saved policy config row")
    thresholds: PolicyThresholdModel = Field(
        ...,
        description="Saved threshold config row",
    )


class PolicyPublishRequest(BaseModel):
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Policy version to publish")
    published_by: Optional[str] = Field(None, description="User publishing the policy")


class PolicyPublishResponse(BaseModel):
    status: str = Field(..., description="Publish status")
    domain: DomainType = Field(..., description="Business domain")
    policy_version: str = Field(..., description="Published policy version")
    published_by: Optional[str] = Field(
        None,
        description="User who published the policy",
    )
    published_at: Optional[str] = Field(None, description="Publish timestamp")
    config_rows_activated: Optional[int] = Field(
        None,
        description="Activated policy config row count",
    )
    threshold_rows_activated: Optional[int] = Field(
        None,
        description="Activated threshold row count",
    )
    risk_rule_rows_activated: Optional[int] = Field(
        None,
        description="Activated risk rule row count",
    )


# -------------------------------------------------------------------
# Governance models
# -------------------------------------------------------------------

class GovernanceKPI(BaseModel):
    total_datasets: int
    certified_datasets: int
    ready_for_certification: int
    in_progress_certifications: int
    avg_fair_score: float
    open_governance_issues: int
    total_checks: int
    passed_checks: int
    failed_checks: int
    check_pass_rate: float
    active_policies: int
    recent_policy_changes_30d: int


class GovernanceDatasetStatus(BaseModel):
    dataset_id: str
    dataset_name: str
    domain: Optional[str] = None
    subdomain: Optional[str] = None
    data_owner: Optional[str] = None
    registry_steward_name: Optional[str] = None
    registry_pod_owner: Optional[str] = None
    certification_status: Optional[str] = None
    certification_tier: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    pass_fail_status: Optional[str] = None
    certified_for_use: Optional[bool] = None
    lineage_complete: Optional[bool] = None
    provenance_complete: Optional[bool] = None
    ready_for_certification: Optional[bool] = None
    certification_blocker_reason: Optional[str] = None
    certification_readiness_band: Optional[str] = None
    certification_readiness_score: Optional[float] = None
    dq_health_score: Optional[float] = None
    dq_risk_score: Optional[float] = None
    automation_readiness_score: Optional[float] = None
    total_findings: Optional[int] = None
    critical_findings: Optional[int] = None
    high_findings: Optional[int] = None
    medium_findings: Optional[int] = None
    low_findings: Optional[int] = None
    failed_rule_count: Optional[int] = None
    failed_checks: int = 0
    fair_overall_score: Optional[float] = None
    fair_maturity_band: Optional[str] = None
    approved_by: Optional[str] = None
    certification_steward_name: Optional[str] = None
    certification_pod_owner: Optional[str] = None
    event_timestamp: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    certification_effective_at: Optional[datetime] = None
    certification_expires_at: Optional[datetime] = None
    recertification_due_at: Optional[datetime] = None


class GovernanceBlocker(BaseModel):
    blocker_reason: str
    blocker_count: int


class GovernancePolicyActivity(BaseModel):
    change_id: Optional[str] = None
    domain: Optional[str] = None
    policy_version: Optional[str] = None
    policy_name: Optional[str] = None
    change_type: Optional[str] = None
    changed_object: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[datetime] = None
    publish_status: Optional[str] = None


class GovernanceOverviewResponse(BaseModel):
    kpis: GovernanceKPI
    dataset_statuses: List[GovernanceDatasetStatus]
    top_blockers: List[GovernanceBlocker]
    policy_activity: List[GovernancePolicyActivity]


# -------------------------------------------------------------------
# Feedback / metrics / DQ models
# -------------------------------------------------------------------

class MatchFeedbackRequest(BaseModel):
    explanation_id: str = Field(
        ...,
        description="Explanation ID returned by /match/explain",
    )
    steward_decision: DecisionType = Field(..., description="Steward final decision")
    steward_override_reason: Optional[str] = Field(
        None,
        description="Legacy free-text reason if steward disagrees or adds context",
    )
    steward_user: str = Field(..., description="Steward identifier or login")

    override_reason_code: Optional[OverrideReasonCode] = Field(
        None,
        description="Structured override reason code",
    )
    override_reason_note: Optional[str] = Field(
        None,
        description="Additional steward note",
    )
    domain: Optional[DomainType] = Field(None, description="Business domain")
    policy_version: Optional[str] = Field(None, description="Policy version used")
    request_id: Optional[str] = Field(
        None,
        description="Client or server generated request ID",
    )


class MatchFeedbackResponse(BaseModel):
    decision_id: str = Field(..., description="Unique ID for steward decision event")
    explanation_id: str = Field(..., description="Explanation being responded to")
    status: str = Field(..., description="Decision processing status")
    override_flag: str = Field(
        ...,
        description="Whether steward overrode recommendation",
    )
    feedback_event_id: Optional[str] = Field(
        None,
        description="Persisted feedback event ID",
    )
    feedback_at: Optional[str] = Field(None, description="Feedback timestamp")
    recommended_action: Optional[DecisionType] = Field(
        None,
        description="Original AI recommendation",
    )
    audit_packet_id: Optional[str] = Field(
        None,
        description="Audit or evidence packet ID",
    )
    request_id: Optional[str] = Field(None, description="Request correlation ID")
    submitted_at: str = Field(..., description="Decision submission timestamp")


class TopItem(BaseModel):
    name: str
    count: int


class ScoreBucketBreakdown(BaseModel):
    bucket: str
    reviewed: int
    false_positives: int
    false_negatives: int
    fp_rate: Optional[float] = None
    fn_rate: Optional[float] = None


class MetricsOverviewResponse(BaseModel):
    days: int = Field(..., description="Lookback window in days")
    total_explanations: int

    decisions: Dict[str, int]
    risk_flags: Dict[str, int]
    recommended_actions: Dict[str, int]
    score_buckets: Dict[str, int]

    avg_ai_confidence: Optional[float] = None
    override_rate: Optional[float] = None
    avg_time_to_feedback_minutes: Optional[float] = None

    top_triggered_rules: List[TopItem]
    top_override_reasons: List[TopItem]

    total_reviewed: int
    true_positives: int
    true_negatives: int
    false_positives: int
    false_negatives: int
    review_or_other: int

    fp_rate: Optional[float] = None
    fn_rate: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None

    top_fp_rules: List[TopItem] = Field(default_factory=list)
    top_fn_rules: List[TopItem] = Field(default_factory=list)
    fpfn_by_score_bucket: List[ScoreBucketBreakdown] = Field(default_factory=list)

    domain: Optional[DomainType] = None
    policy_version: Optional[str] = None
    generated_at: Optional[str] = None
    request_id: Optional[str] = None
    learning_timeline: List[LearningTimelineItem] = Field(default_factory=list)


class DqDashboardRow(BaseModel):
    metric_date: date
    domain: str

    total_records: int
    avg_record_score: Optional[float] = None
    records_below_threshold: int
    records_below_threshold_rate: Optional[float] = None

    dq_health_score: Optional[float] = None
    dq_risk_score: Optional[float] = None
    automation_readiness_score: Optional[float] = None

    avg_completeness_score: Optional[float] = None
    avg_validity_score: Optional[float] = None
    avg_standardization_score: Optional[float] = None
    avg_consistency_score: Optional[float] = None
    avg_uniqueness_score: Optional[float] = None

    total_findings: int
    critical_findings: int
    high_findings: int
    medium_findings: int
    low_findings: int

    open_findings_count: int
    accepted_findings_count: int
    resolved_findings_count: int
    waived_findings_count: int
    records_with_findings: int

    failed_rule_count: int
    duplicate_record_count: int

    scored_critical_issue_count: int
    scored_high_issue_count: int
    scored_medium_issue_count: int
    scored_low_issue_count: int
    scored_total_issue_count: int

    records_flagged_by_ai: int
    ai_recommendations_generated: int
    open_ai_recommendations: int
    accepted_ai_recommendations: int
    rejected_ai_recommendations: int
    implemented_ai_recommendations: int
    high_priority_recommendations: int
    medium_priority_recommendations: int
    low_priority_recommendations: int
    avg_ai_recommendation_confidence: Optional[float] = None
    ai_rules_flagged_count: int

    steward_actions_taken: int
    automated_fixes_applied: int

    scored_record_count: int
    min_record_score: Optional[float] = None
    max_record_score: Optional[float] = None
    very_low_score_count: int
    low_score_count: int
    medium_score_count: int
    high_score_count: int

    total_rules_executed: int
    rules_triggered: int
    configured_rule_count: int
    active_rule_count: int
    inactive_rule_count: int
    configured_critical_rules: int
    configured_high_rules: int
    configured_medium_rules: int
    configured_low_rules: int

    validity_rule_count: int
    completeness_rule_count: int
    standardization_rule_count: int
    uniqueness_rule_count: int
    consistency_rule_count: int
    avg_rule_weight: Optional[float] = None

    findings_per_record_rate: Optional[float] = None
    duplicate_rate: Optional[float] = None
    ai_flagged_record_rate: Optional[float] = None
    automated_fix_rate: Optional[float] = None
    steward_action_rate: Optional[float] = None
    findings_resolution_rate: Optional[float] = None
    ai_recommendation_acceptance_rate: Optional[float] = None
    ai_recommendation_implementation_rate: Optional[float] = None
    impacted_record_rate: Optional[float] = None

    summary_created_at: Optional[str] = None


class DqDashboardResponse(BaseModel):
    days: int
    domain: Optional[str] = None
    rows: List[DqDashboardRow]
    latest: Optional[DqDashboardRow] = None
    generated_at: Optional[str] = None