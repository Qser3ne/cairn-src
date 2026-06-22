from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ProjectKind = Literal["recon", "vuln"]
AuthMode = Literal["anonymous", "authenticated", "dual"]
ProjectStatus = Literal["active", "stopped", "completed"]
JudgeStatus = Literal["not_judged", "ready", "not_ready", "blocked"]
ResearchValue = Literal["unknown", "high", "medium", "low", "none"]
FindingNextAction = Literal["triage", "follow_up", "report", "close"]
ReportStatus = Literal["not_started", "queued", "drafted", "submitted", "closed"]
IntentKind = Literal["explore", "report"]
AuthScope = Literal["anonymous", "authenticated"]
EphemeralJobStatus = Literal["queued", "running", "succeeded", "failed", "expired"]


class Settings(BaseModel):
    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)


class Fact(BaseModel):
    id: str
    description: str


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
    finding_id: str | None = None
    auth_scope: AuthScope | None = None

    model_config = {"populate_by_name": True}


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str


class ProjectCookie(BaseModel):
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
    reason_pending: bool = False
    recon_max_reason_rounds: int | None = None
    recon_reason_rounds: int = 0
    recon_explore_rounds: int = 0
    recon_stable_rounds: int = 0
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
    project_kind: ProjectKind = "recon"
    auth_mode: AuthMode | None = None
    parent_project_id: str | None = None
    parent_snapshot_id: str | None = None
    recon_max_reason_rounds: int | None = 8
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
        if self.project_kind == "recon":
            if self.parent_project_id is not None or self.parent_snapshot_id is not None:
                raise ValueError("recon project cannot have parent_project_id or parent_snapshot_id")
            if self.auth_mode in ("anonymous", "authenticated"):
                raise ValueError("recon project auth_mode is fixed to dual")
            self.auth_mode = "dual"
            if not accounts:
                raise ValueError("recon project requires at least one cookie session")
        if self.project_kind == "vuln":
            if self.auth_mode is None:
                self.auth_mode = "anonymous"
            if self.auth_mode == "dual":
                raise ValueError("vuln project auth_mode must be anonymous or authenticated")
            if not self.parent_project_id:
                raise ValueError("vuln project requires parent_project_id")
            if not self.parent_snapshot_id:
                raise ValueError("vuln project requires parent_snapshot_id")
        if self.auth_mode != "authenticated" and accounts:
            if self.project_kind != "recon":
                raise ValueError("accounts are only supported for authenticated projects")
        if self.auth_mode == "authenticated" and not accounts:
            raise ValueError("authenticated projects require at least one cookie session")
        return self


class CreateHintRequest(BaseModel):
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
    finding_id: str | None = None
    auth_scope: AuthScope | None = None

    model_config = {"populate_by_name": True}

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
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        return cleaned


class HeartbeatRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReasonClaimRequest(BaseModel):
    worker: str
    trigger: str

    @field_validator("worker", "trigger")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeRequest(BaseModel):
    worker: str
    description: str
    findings: list[FindingCreate] | None = None

    @field_validator("worker", "description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeResponse(BaseModel):
    fact: Fact
    intent: Intent
    findings: list[Finding] = Field(default_factory=list)


class UpdateProjectStatusRequest(BaseModel):
    status: ProjectStatus


class UpdateProjectTitleRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReconReasonRoundRequest(BaseModel):
    stable: bool = False


class ProjectSnapshotCreate(BaseModel):
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
    result: dict[str, Any] | None = None
    error: str | None = None
    worker: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str


class EphemeralJobClaimRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class EphemeralJobFinishRequest(BaseModel):
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
