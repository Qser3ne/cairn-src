from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ProjectMode = Literal["standard", "src"]
ProjectAuthMode = Literal["anonymous", "authenticated"]


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
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        return text


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

    model_config = {"populate_by_name": True}


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str


class ProjectAccount(BaseModel):
    id: str
    label: str
    username: str
    password: str


class ProjectAccountCreate(BaseModel):
    label: str | None = None
    username: str
    password: str

    @field_validator("label")
    @classmethod
    def validate_optional_account_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        return text

    @field_validator("username", "password")
    @classmethod
    def validate_required_account_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ProjectReason(BaseModel):
    worker: str
    trigger: str
    started_at: str
    last_heartbeat_at: str


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    mode: ProjectMode = "standard"
    auth_mode: ProjectAuthMode = "anonymous"
    bootstrap_enabled: bool
    created_at: str
    reason: ProjectReason | None = None


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
    title: str
    origin: str
    goal: str
    mode: ProjectMode = "standard"
    auth_mode: ProjectAuthMode = "anonymous"
    bootstrap_enabled: bool | None = None
    hints: list[CreateHintInline] | None = None
    accounts: list[ProjectAccountCreate] | None = None

    @field_validator("title", "origin", "goal")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @model_validator(mode="after")
    def validate_auth_mode_accounts(self) -> "CreateProjectRequest":
        if self.mode != "src" and self.auth_mode != "anonymous":
            raise ValueError("auth_mode=authenticated is only supported for src projects")
        accounts = self.accounts or []
        if self.auth_mode != "authenticated" and accounts:
            raise ValueError("accounts are only supported for authenticated src projects")
        if self.auth_mode == "authenticated" and not accounts:
            raise ValueError("authenticated src projects require at least one account")
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


class CompleteRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    worker: str

    model_config = {"populate_by_name": True}

    @field_validator("description", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
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


class ConcludeResponse(BaseModel):
    fact: Fact
    intent: Intent
    findings: list[Finding] = Field(default_factory=list)


class UpdateProjectStatusRequest(BaseModel):
    status: Literal["active", "stopped"]


class UpdateProjectTitleRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenRequest(BaseModel):
    description: str
    creator: str

    @field_validator("description", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenResponse(BaseModel):
    project: ProjectMeta
    fact: Fact
    intent: Intent
