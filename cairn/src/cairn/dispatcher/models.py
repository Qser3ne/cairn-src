from __future__ import annotations

from dataclasses import dataclass

from cairn.server.models import ProjectAccount, TaskMode

from cairn.dispatcher.runtime.cancellation import TaskCancellation


@dataclass(slots=True)
class RunningTask:
    project_id: str
    task_type: str
    worker_name: str
    cancellation: TaskCancellation
    intent_id: str | None = None
    account: ProjectAccount | None = None
    reason_trigger: str | None = None
    reason_task_mode: TaskMode | None = None
    # Reason tasks keep submit-time counts only as a fallback. The durable
    # checkpoint should describe the graph baseline after a successful task.
    reason_start_fact_count: int | None = None
    reason_start_hint_count: int | None = None
    reason_start_open_intent_count: int | None = None


@dataclass(slots=True)
class ReasonCheckpoint:
    fact_count: int
    hint_count: int
    open_intent_count: int
    task_mode: TaskMode = "collection"
