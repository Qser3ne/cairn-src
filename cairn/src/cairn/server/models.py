from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ProjectKind = Literal["vuln"]
TaskMode = Literal["collection", "validation", "report"]
AuthMode = Literal["anonymous", "authenticated", "dual"]
ProjectStatus = Literal["active", "stopped", "completed"]
JudgeStatus = Literal["not_judged", "ready", "not_ready", "blocked"]
ResearchValue = Literal["unknown", "high", "medium", "low", "none"]
FindingNextAction = Literal["triage", "follow_up", "report", "close"]
ReportStatus = Literal["not_started", "queued", "drafted", "submitted", "closed"]
IntentKind = Literal["explore", "report"]
AuthScope = Literal["anonymous", "authenticated"]
EphemeralJobStatus = Literal["queued", "running", "succeeded", "failed", "expired"]
FactType = Literal["observation", "feature_surface"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    initial_collection_rounds: int = Field(ge=0)
    collection_worker_limit: int = Field(ge=1)


class Fact(BaseModel):
    id: str
    description: str
    fact_type: FactType = "observation"
    title: str | None = None
    summary: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    id: str
    title: str
    vulnerability_type: str
    severity: str
    target: str
    location: str
    impact: str
    evidence: str
    reproduction: str
    remediation: str
    status: str
    research_value: ResearchValue = "unknown"
    next_action: FindingNextAction = "triage"
    followup_reason: str = ""
    followup_intent_description: str = ""
    followup_intent_id: str | None = None
    report_status: ReportStatus = "not_started"
    report_intent_id: str | None = None
    triaged_at: str | None = None
    fact_id: str
    intent_id: str
    created_at: str


class FindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    vulnerability_type: str = "unknown"
    severity: str = "unknown"
    target: str = ""
    location: str = ""
    impact: str = ""
    evidence: str = ""
    reproduction: str = ""
    remediation: str = ""
    status: str = "open"
    research_value: ResearchValue = "unknown"
    next_action: FindingNextAction = "triage"
    followup_reason: str = ""
    followup_intent_description: str = ""

    @field_validator(
        "title",
        "vulnerability_type",
        "severity",
        "target",
        "location",
        "impact",
        "evidence",
        "reproduction",
        "remediation",
        "status",
        "followup_reason",
        "followup_intent_description",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        return text

    @model_validator(mode="after")
    def validate_next_action(self) -> "FindingCreate":
        if self.next_action == "follow_up" and not self.followup_intent_description.strip():
            raise ValueError("followup_intent_description is required when next_action=follow_up")
        return self


class Intent(BaseModel):
    id: str
    from_: list[str] = Field(alias="from")
    to: str | None = None
    description: str
    creator: str
    worker: str | None = None
    last_heartbeat_at: str | None = None
    created_at: str
    concluded_at: str | None = None
    intent_kind: IntentKind = "explore"
    task_mode: TaskMode = "validation"
    finding_id: str | None = None
    auth_scope: AuthScope | None = None

    model_config = {"populate_by_name": True}


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str


class ProjectCookie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str

    @field_validator("name", "value")
    @classmethod
    def validate_required_cookie_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ProjectAccount(BaseModel):
    id: str
    label: str
    cookies: list[ProjectCookie] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cookie_names_unique(self) -> "ProjectAccount":
        names = [cookie.name for cookie in self.cookies]
        if len(names) != len(set(names)):
            raise ValueError("cookie names must be unique within a session")
        return self


class ProjectAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    cookies: list[ProjectCookie] = Field(min_length=1)

    @field_validator("label")
    @classmethod
    def validate_optional_account_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        return text

    @model_validator(mode="after")
    def validate_cookie_names_unique(self) -> "ProjectAccountCreate":
        names = [cookie.name for cookie in self.cookies]
        if len(names) != len(set(names)):
            raise ValueError("cookie names must be unique within a session")
        return self


class ProjectReason(BaseModel):
    worker: str
    trigger: str
    started_at: str
    last_heartbeat_at: str


def empty_project_reasons() -> dict[TaskMode, ProjectReason | None]:
    return {"collection": None, "validation": None, "report": None}


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: ProjectStatus
    project_kind: ProjectKind
    auth_mode: AuthMode = "anonymous"
    parent_project_id: str | None = None
    parent_snapshot_id: str | None = None
    created_at: str
    reason: ProjectReason | None = None
    reasons: dict[TaskMode, ProjectReason | None] = Field(default_factory=empty_project_reasons)
    reason_pending: bool = False
    collection_reason_rounds: int = 0
    collection_explore_rounds: int = 0
    collection_stable_rounds: int = 0
    judge_status: JudgeStatus = "not_judged"
    judged_at: str | None = None


class ProjectSummary(ProjectMeta):
    fact_count: int
    intent_count: int
    working_intent_count: int
    unclaimed_intent_count: int
    hint_count: int
    finding_count: int


class ProjectDetail(BaseModel):
    project: ProjectMeta
    facts: list[Fact]
    intents: list[Intent]
    hints: list[Hint]
    findings: list[Finding] = Field(default_factory=list)
    accounts: list[ProjectAccount] = Field(default_factory=list)


class CreateHintInline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    origin: str
    project_kind: ProjectKind = "vuln"
    auth_mode: AuthMode | None = None
    parent_project_id: str | None = None
    parent_snapshot_id: str | None = None
    hints: list[CreateHintInline] | None = None
    accounts: list[ProjectAccountCreate] | None = None

    @field_validator("title", "origin")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @model_validator(mode="after")
    def validate_project_kind_and_accounts(self) -> "CreateProjectRequest":
        accounts = self.accounts or []
        if self.auth_mode is None:
            self.auth_mode = "dual" if accounts else "anonymous"
        if self.auth_mode == "anonymous" and accounts:
            raise ValueError("anonymous projects cannot include accounts")
        if self.auth_mode in ("authenticated", "dual") and not accounts:
            raise ValueError(f"{self.auth_mode} projects require at least one cookie session")
        if (self.parent_project_id is None) != (self.parent_snapshot_id is None):
            raise ValueError("parent_project_id and parent_snapshot_id must be provided together")
        return self


class CreateHintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateIntentRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    creator: str
    worker: str | None = None
    intent_kind: IntentKind = "explore"
    task_mode: TaskMode | None = None
    finding_id: str | None = None
    auth_scope: AuthScope | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("description", "creator", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            if text in seen:
                raise ValueError("fact ids must be unique")
            seen.add(text)
            cleaned.append(text)
        return cleaned


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReasonClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    trigger: str
    task_mode: TaskMode

    @field_validator("worker", "trigger")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReasonHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    task_mode: TaskMode

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    description: str
    fact_type: FactType = "observation"
    title: str | None = None
    summary: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    findings: list[FindingCreate] | None = None

    @field_validator("worker", "description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("title", "summary")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class ConcludeResponse(BaseModel):
    fact: Fact
    intent: Intent
    findings: list[Finding] = Field(default_factory=list)


class UpdateProjectStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProjectStatus


class UpdateProjectTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CollectionReasonRoundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable: bool = False


class ProjectSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_type: str = "recon_fork"
    selected_fact_ids: list[str] = Field(default_factory=list)

    @field_validator("snapshot_type")
    @classmethod
    def validate_snapshot_type(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ProjectSnapshot(BaseModel):
    id: str
    project_id: str
    snapshot_type: str
    summary_yaml: str
    selected_fact_ids: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ForkVulnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    auth_mode: AuthMode = "anonymous"
    snapshot_id: str
    candidate_limit: int | None = Field(default=None, ge=1)
    accounts: list[ProjectAccountCreate] | None = None

    @field_validator("title", "snapshot_id")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @model_validator(mode="after")
    def validate_accounts(self) -> "ForkVulnRequest":
        accounts = self.accounts or []
        if self.auth_mode == "dual":
            raise ValueError("vuln project auth_mode must be anonymous or authenticated")
        if self.auth_mode != "authenticated" and accounts:
            raise ValueError("accounts are only supported for authenticated projects")
        if self.auth_mode == "authenticated" and not accounts:
            raise ValueError("authenticated vuln project requires at least one cookie session")
        return self


class ForkVulnSeedJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    auth_mode: AuthMode = "anonymous"
    snapshot_id: str
    candidate_limit: int | None = Field(default=8, ge=1)
    accounts: list[ProjectAccountCreate] | None = None

    @field_validator("title", "snapshot_id")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @model_validator(mode="after")
    def validate_accounts(self) -> "ForkVulnSeedJobRequest":
        accounts = self.accounts or []
        if self.auth_mode == "dual":
            raise ValueError("vuln project auth_mode must be anonymous or authenticated")
        if self.auth_mode != "authenticated" and accounts:
            raise ValueError("accounts are only supported for authenticated projects")
        if self.auth_mode == "authenticated" and not accounts:
            raise ValueError("authenticated vuln project requires at least one cookie session")
        return self


class ForkSeedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    auth_scope: AuthScope
    candidate_type: str
    derived_from: list[str] = Field(min_length=1)
    description: str
    feature_summary: str | None = None
    user_actions: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    apis: list[str] = Field(default_factory=list)
    vuln_validation_focus: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("title", "candidate_type", "description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("feature_summary")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator(
        "user_actions",
        "routes",
        "apis",
        "vuln_validation_focus",
        "known_constraints",
        "evidence_refs",
    )
    @classmethod
    def validate_text_list(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if text:
                cleaned.append(text)
        return cleaned

    @field_validator("derived_from")
    @classmethod
    def validate_derived_from(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("derived_from fact ids must not be empty")
            cleaned.append(text)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("derived_from fact ids must be unique")
        return cleaned


class ForkSeedFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    seed_facts: list[ForkSeedFact] = Field(min_length=1, max_length=10)

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ForkSeedJobCreateResponse(BaseModel):
    job_id: str
    status: str


class JudgementCreateResponse(BaseModel):
    job_id: str
    status: str


class JudgementResult(BaseModel):
    id: str
    status: EphemeralJobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    worker: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str


class EphemeralJob(BaseModel):
    id: str
    project_id: str
    job_type: str
    status: EphemeralJobStatus
    input_snapshot_yaml: str
    input: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    worker: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str


class EphemeralJobClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class EphemeralJobFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    result: dict[str, Any] = Field(default_factory=dict)

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class EphemeralJobFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    error: str

    @field_validator("worker", "error")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class FindingReport(BaseModel):
    id: str
    project_id: str
    finding_id: str
    intent_id: str
    report_markdown: str
    report_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ReportConcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    report_markdown: str
    report_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("worker", "report_markdown")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text
