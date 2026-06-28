from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ProjectKind = Literal["vuln"]
ProjectStatus = Literal["active", "stopped", "completed"]
AuthMode = Literal["anonymous", "authenticated", "dual"]
AuthScope = Literal["anonymous", "authenticated"]
TaskMode = Literal["collection", "vulnerability", "report"]
TaskType = Literal["collection_task", "vulnerability_task"]
FactType = Literal["collection_fact", "vulnerability_fact"]
FindingType = Literal["findings"]
EphemeralJobStatus = Literal["queued", "running", "succeeded", "failed", "expired"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    initial_collection_rounds: int = Field(ge=0)
    collection_worker_limit: int = Field(ge=1)


class Origin(BaseModel):
    id: Literal["origin"] = "origin"
    description: str


class Task(BaseModel):
    id: str
    type: TaskType
    description: str
    creation_time: str
    completion_time: str | None = None
    from_: list[str] = Field(alias="from")
    to: list[str] = Field(default_factory=list)
    worker: str | None = None
    last_heartbeat_at: str | None = None
    auth_scope: AuthScope | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @property
    def task_mode(self) -> TaskMode:
        return "collection" if self.type == "collection_task" else "vulnerability"

    @task_mode.setter
    def task_mode(self, value: str) -> None:
        self.type = "collection_task" if value == "collection" else "vulnerability_task"

    @property
    def created_at(self) -> str:
        return self.creation_time

    @property
    def concluded_at(self) -> str | None:
        return self.completion_time


class Fact(BaseModel):
    id: str
    type: FactType = "collection_fact"
    description: str
    creation_time: str = "2026-01-01T00:00:00Z"
    from_: list[str] = Field(default_factory=lambda: ["origin"], alias="from")
    from_task: str = "t0"
    to: list[str] = Field(default_factory=list)
    evidence: str = "legacy:test"

    model_config = ConfigDict(populate_by_name=True)


class Finding(BaseModel):
    id: str
    type: FindingType = "findings"
    description: str
    creation_time: str
    from_: list[str] = Field(alias="from")
    from_task: str
    to: list[str] = Field(default_factory=list)
    report: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class FindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str

    @field_validator("description")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


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
    return {"collection": None, "vulnerability": None, "report": None}


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


class ProjectSummary(ProjectMeta):
    fact_count: int
    task_count: int = 0
    working_task_count: int = 0
    unclaimed_task_count: int = 0
    hint_count: int
    finding_count: int


class ProjectDetail(BaseModel):
    project: ProjectMeta
    origin: Origin
    tasks: list[Task]
    facts: list[Fact]
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


class CreateTaskRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    type: TaskType
    description: str
    worker: str | None = None
    auth_scope: AuthScope | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("description", "worker")
    @classmethod
    def validate_optional_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("sources must not be empty")
            if text in seen:
                raise ValueError("sources must be unique")
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


class ConcludeTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    description: str
    evidence: str
    findings: list[FindingCreate] | None = None

    @field_validator("worker", "description", "evidence")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeTaskResponse(BaseModel):
    fact: Fact
    task: Task
    findings: list[Finding] = Field(default_factory=list)


class ReportPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    report: str

    @field_validator("worker", "report")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


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

    snapshot_type: str = "blackboard"
    selected_fact_ids: list[str] = Field(default_factory=list)


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


class ForkVulnSeedJobRequest(ForkVulnRequest):
    candidate_limit: int | None = Field(default=8, ge=1)


class ForkSeedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    auth_scope: AuthScope
    candidate_type: str
    derived_from: list[str] = Field(min_length=1)
    description: str


class ForkSeedFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    seed_facts: list[ForkSeedFact] = Field(min_length=1, max_length=10)


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


class EphemeralJobFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    result: dict[str, Any] = Field(default_factory=dict)


class EphemeralJobFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    error: str
