from __future__ import annotations

import logging
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.models import ReasonCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import CairnClient, ProtocolError
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HEARTBEAT_FAILURE_GRACE_MULTIPLIER
from cairn.dispatcher.runtime.startup_healthcheck import format_failure_summary, run_startup_healthchecks
from cairn.dispatcher.scheduler.worker_select import choose_worker
from cairn.dispatcher.tasks.explore import run_explore_task
from cairn.dispatcher.tasks.reason import run_reason_task
from cairn.dispatcher.tasks.report import run_report_task
from cairn.server.models import Finding, ProjectDetail, ProjectSummary, Settings, Task, TaskMode

LOG = logging.getLogger(__name__)
UNHEALTHY_RETRY_AFTER_SECONDS = 5
REJECTED_RETRY_AFTER_SECONDS = 5
PROJECT_FAILURE_STOP_THRESHOLD = 3


@dataclass(slots=True)
class WorkerSelection:
    worker: WorkerConfig | None
    blocked_busy: list[str]
    blocked_unhealthy: list[str]
    blocked_rejected: list[str]
    blocked_task_type: list[str]


class DispatcherLoop:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = DispatchConfig.load(config_path)
        self.client = CairnClient(self.config.server)
        self.container_manager = ContainerManager(self.config.container)
        self.executor = ThreadPoolExecutor(max_workers=self.config.runtime.max_workers)
        self.cleanup_executor = ThreadPoolExecutor(max_workers=max(1, min(8, self.config.runtime.max_workers)))
        self.futures: dict[Future[str], RunningTask] = {}
        self.cleanup_futures: dict[Future[bool], tuple[str, str | None, str | None]] = {}
        self.reason_checkpoints: dict[tuple[str, TaskMode], ReasonCheckpoint] = {}
        self.collection_expansion_requests: dict[str, str] = {}
        self.collection_warmup_released: set[str] = set()
        self.authenticated_wait_queues: dict[str, deque[str]] = {}
        self.account_leases: dict[str, dict[str, str]] = {}
        self.runtime_project_ids: set[str] = set()
        self.worker_unhealthy_until: dict[str, float] = {}
        self.worker_rejected_until: dict[tuple[str, str, str], float] = {}
        self.project_failure_counts: dict[str, int] = {}
        self.server_settings: Settings | None = None
        self._log_state: dict[str, tuple[int, str, tuple[object, ...]]] = {}
        self._cleanup_pending: set[str] = set()
        self._inactive_cleanup_done: dict[str, str] = {}
        self.project_cursor = 0
        self._settings_checked = False
        self._startup_healthchecks_checked = False

    def close(self) -> None:
        if self.futures:
            LOG.info(
                "dispatcher shutting down waiting_for_tasks=%s running_projects=%s",
                len(self.futures),
                sorted({task.project_id for task in self.futures.values()}),
            )
        self.executor.shutdown(wait=True)
        self.cleanup_executor.shutdown(wait=True)
        self.container_manager.close()
        self.client.close()

    def run(self, once: bool = False) -> None:
        try:
            self.run_startup_healthchecks()
            while True:
                try:
                    settings = self.client.get_settings()
                    self.server_settings = settings
                    if not self._settings_checked:
                        self._validate_server_settings(settings)
                        self._settings_checked = True
                    self._reap_futures()
                    self._reap_cleanup_futures()
                    summaries = self.client.list_projects()
                    self._initialize_reason_checkpoints(summaries)
                    self._refresh_runtime_projects(summaries)
                    self._cleanup_authenticated_wait_queues(summaries)
                    self._cancel_inactive_tasks(summaries)
                    self._queue_container_cleanups(summaries)
                    self._dispatch_available(summaries)
                except (requests.RequestException, ProtocolError) as exc:
                    if once:
                        raise
                    LOG.warning(
                        "dispatcher server request failed error=%s retry_in=%ss",
                        exc,
                        self.config.runtime.interval,
                    )
                    time.sleep(self.config.runtime.interval)
                    continue
                if once:
                    break
                time.sleep(self.config.runtime.interval)
        finally:
            self.close()

    def run_startup_healthchecks_only(self) -> None:
        try:
            self.run_startup_healthchecks(show_commands=True, force=True)
        finally:
            self.close()

    def run_startup_healthchecks(self, *, show_commands: bool = False, force: bool = False) -> None:
        if self._startup_healthchecks_checked:
            return
        if not force and self.config.runtime.worker_healthcheck == "disabled":
            LOG.info("skip startup worker healthchecks because runtime.worker_healthcheck=disabled")
            self._startup_healthchecks_checked = True
            return
        self._run_startup_healthchecks(show_commands=show_commands)
        self._startup_healthchecks_checked = True

    def _dispatch_available(self, summaries: list[ProjectSummary]) -> None:
        self._sync_authenticated_wait_queues(summaries)
        if len(self.futures) >= self.config.runtime.max_workers:
            self._log_changed(
                "dispatch/global",
                logging.INFO,
                "skip dispatch because max_workers reached running_tasks=%s",
                len(self.futures),
            )
            return
        active = [summary for summary in summaries if summary.status == "active"]
        if not active:
            self._log_changed("dispatch/global", logging.INFO, "skip dispatch because no active projects")
            return

        running_projects = self._ordered_projects(
            [summary for summary in active if summary.id in self.runtime_project_ids]
        )
        idle_projects = self._ordered_projects(
            [summary for summary in active if summary.id not in self.runtime_project_ids]
        )

        dispatched = True
        while dispatched and len(self.futures) < self.config.runtime.max_workers:
            dispatched = False
            for summary in running_projects:
                if self._try_dispatch_project(summary):
                    dispatched = True
                    if len(self.futures) >= self.config.runtime.max_workers:
                        return
            if dispatched:
                continue
            if self._running_project_count(active) >= self.config.runtime.max_running_projects:
                self._log_changed(
                    "dispatch/idle-limit",
                    logging.INFO,
                    "skip idle project dispatch because max_running_projects reached running_projects=%s",
                    self._running_project_count(active),
                )
                return
            for summary in idle_projects:
                if self._running_project_count(active) >= self.config.runtime.max_running_projects:
                    self._log_changed(
                        "dispatch/idle-limit",
                        logging.INFO,
                        "stop idle project dispatch because max_running_projects reached running_projects=%s",
                        self._running_project_count(active),
                    )
                    return
                if self._try_dispatch_project(summary):
                    dispatched = True
                    break

    def _dispatch_judge_jobs(self) -> None:
        return

    def _dispatch_fork_seed_jobs(self) -> None:
        return

    def _sync_authenticated_wait_queues(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "active":
                continue
            if summary.unclaimed_task_count <= 0:
                continue
            try:
                project = self.client.get_project(summary.id)
            except requests.RequestException:
                raise
            if not self._project_uses_account_pool(project):
                continue
            self._enqueue_current_authenticated_waiters(project)

    def _ordered_projects(self, summaries: list[ProjectSummary]) -> list[ProjectSummary]:
        if not summaries:
            return []
        ids = [summary.id for summary in summaries]
        ids.sort()
        offset = self.project_cursor % len(ids)
        ordered_ids = ids[offset:] + ids[:offset]
        by_id = {summary.id: summary for summary in summaries}
        self.project_cursor += 1
        return [by_id[project_id] for project_id in ordered_ids]

    def _try_dispatch_project(self, summary: ProjectSummary) -> bool:
        skip_scope = f"project:{summary.id}:skip"
        container_name = self.container_manager.container_name(summary.id)
        if container_name in self._cleanup_pending:
            self._log_changed(
                f"{skip_scope}:cleanup_pending",
                logging.DEBUG,
                "skip project=%s because container cleanup is still pending container=%s",
                summary.id,
                container_name,
            )
            return False
        project = self.client.get_project(summary.id)
        if project.project.status != "active":
            self._log_changed(
                f"{skip_scope}:status",
                logging.INFO,
                "skip project=%s because status=%s",
                summary.id,
                project.project.status,
            )
            return False
        if self._is_initial_project(project):
            if self._reason_claimed(project, "collection") is not None:
                return False
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_reason(project, export_yaml, "initial", "collection")
        self._enqueue_current_authenticated_waiters(project)
        if self._project_running_task_count(summary.id) >= self.config.runtime.max_project_workers:
            self._log_changed(
                f"{skip_scope}:max_project_workers",
                logging.INFO,
                "skip project=%s because max_project_workers reached running_tasks=%s",
                summary.id,
                self._project_running_task_summary(summary.id),
            )
            return False
        if len(self.futures) >= self.config.runtime.max_workers:
            self._log_changed(
                "dispatch/global",
                logging.INFO,
                "skip dispatch because max_workers reached running_tasks=%s",
                len(self.futures),
            )
            return False
        collection_warmup_complete = self._collection_warmup_complete(project)
        queued_intent = None if not collection_warmup_complete else self._next_authenticated_waiting_intent(project)
        if queued_intent is not None:
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_explore(project, export_yaml, queued_intent)
        if collection_warmup_complete:
            report_finding = self._next_unreported_finding(project)
            if report_finding is not None:
                export_yaml = self.client.export_project(summary.id)
                if self._dispatch_report(project, export_yaml, report_finding):
                    return True
        running_intent_ids = self._project_running_explore_intents(summary.id)
        unclaimed_intents = [
            intent
            for intent in project.tasks
            if intent.completion_time is None
            and intent.worker is None
            and intent.id not in running_intent_ids
        ]
        if running_intent_ids and not unclaimed_intents:
            self._log_changed(
                f"{skip_scope}:explore_running",
                logging.DEBUG,
                "skip explore project=%s because all unclaimed intents are already running locally intents=%s",
                summary.id,
                sorted(running_intent_ids),
            )
        if unclaimed_intents:
            export_yaml = self.client.export_project(summary.id)
            if not collection_warmup_complete:
                collection_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="collection")
                if collection_intent is not None:
                    return self._dispatch_explore(project, export_yaml, collection_intent)
            else:
                vulnerability_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="vulnerability")
                if vulnerability_intent is not None:
                    return self._dispatch_explore(project, export_yaml, vulnerability_intent)
                collection_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="collection")
                if collection_intent is not None:
                    return self._dispatch_explore(project, export_yaml, collection_intent)
        reason_task_modes: tuple[TaskMode, ...] = ("vulnerability", "collection") if collection_warmup_complete else ("collection",)
        for task_mode in reason_task_modes:
            reason = self._reason_claimed(project, task_mode)
            if reason is not None:
                continue
            reason_trigger = self._reason_trigger(project, task_mode)
            if reason_trigger is not None:
                export_yaml = self.client.export_project(summary.id)
                return self._dispatch_reason(project, export_yaml, reason_trigger, task_mode)
        claimed_reasons = [reason.worker for reason in project.project.reasons.values() if reason is not None]
        if claimed_reasons:
            self._log_changed(
                f"{skip_scope}:reason_claimed",
                logging.DEBUG,
                "skip reason project=%s because reasons are already claimed by %s",
                summary.id,
                claimed_reasons,
            )
            return False
        self._log_changed(
            f"{skip_scope}:graph_unchanged",
            logging.DEBUG,
            "skip reason project=%s because reason state unchanged facts=%s hints=%s open_tasks=%s tasks=%s",
            summary.id,
            len(project.facts),
            len(project.hints),
            self._project_open_task_count(project),
            len(project.tasks),
        )
        return False

    def _dispatch_reason(
        self,
        project: ProjectDetail,
        export_yaml: str,
        trigger: str,
        task_mode: TaskMode | None = None,
    ) -> bool:
        task_mode = task_mode or self._reason_task_mode(project)
        task_type = self._reason_task_type(task_mode)
        if self._is_collection_task_type(task_type) and not self._collection_capacity_available():
            self._log_changed(
                f"project:{project.project.id}:collection_limit:{task_type}",
                logging.INFO,
                "skip %s project=%s because collection_worker_limit reached running_collection_tasks=%s limit=%s",
                task_type,
                project.project.id,
                self._running_collection_task_count(),
                self._current_server_settings().collection_worker_limit,
            )
            return False
        selection = self._select_worker(project.project.id, task_type)
        worker = selection.worker
        if worker is None:
            self._log_changed(
                f"project:{project.project.id}:worker:{task_type}",
                logging.INFO,
                "no worker available for %s project=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                task_type,
                project.project.id,
                selection.blocked_busy,
                selection.blocked_unhealthy,
                selection.blocked_rejected,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:{task_type}")
        claim = self.client.claim_reason(project.project.id, worker.name, trigger, task_mode)
        if claim.status_code in (403, 409):
            level = logging.INFO if claim.status_code == 403 else logging.WARNING
            LOG.log(
                level,
                "reason claim failed project=%s worker=%s status=%s",
                project.project.id,
                worker.name,
                claim.status_code,
            )
            return False
        if not claim.ok:
            LOG.warning(
                "reason claim failed project=%s worker=%s status=%s",
                project.project.id,
                worker.name,
                claim.status_code,
            )
            return False
        try:
            future = self.executor.submit(
                run_reason_task,
                self.config,
                self.client,
                self.container_manager,
                project,
                export_yaml,
                worker,
                task_mode,
                cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("failed to submit reason task project=%s worker=%s", project.project.id, worker.name)
            self._best_effort_release_reason(project.project.id, worker.name, task_mode)
            return False
        self.futures[future] = RunningTask(
            project_id=project.project.id,
            task_type=task_type,
            worker_name=worker.name,
            cancellation=cancellation,
            intent_id=None,
            reason_trigger=trigger,
            reason_task_mode=task_mode,
            reason_start_fact_count=len(project.facts),
            reason_start_hint_count=len(project.hints),
            reason_start_open_task_count=self._project_open_task_count_for_mode(project, task_mode),
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("dispatched reason project=%s worker=%s trigger=%s", project.project.id, worker.name, trigger)
        return True

    def _reason_task_mode(self, project: ProjectDetail) -> TaskMode:
        if project.findings:
            return "vulnerability"
        return "collection"

    def _reason_task_type(self, task_mode: TaskMode) -> str:
        return f"{task_mode}_reason"

    def _newest_unclaimed_intent(
        self,
        intents: list[Task],
        *,
        task_mode: TaskMode | None = None,
    ) -> Task | None:
        candidates = [
            intent
            for intent in intents
            if task_mode is None or intent.task_mode == task_mode
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.created_at, item.id))

    def _reason_claimed(self, project: ProjectDetail, task_mode: TaskMode):
        return project.project.reasons.get(task_mode)

    def _next_unreported_finding(self, project: ProjectDetail) -> Finding | None:
        running = {
            task.intent_id
            for task in self.futures.values()
            if task.project_id == project.project.id and task.task_type == "report"
        }
        for finding in project.findings:
            if finding.report is None and finding.id not in running:
                return finding
        return None

    def _dispatch_report(self, project: ProjectDetail, export_yaml: str, finding: Finding) -> bool:
        selection = self._select_worker(project.project.id, "report")
        worker = selection.worker
        if worker is None:
            self._log_changed(
                f"project:{project.project.id}:worker:report",
                logging.INFO,
                "no worker available for report project=%s finding=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                project.project.id,
                finding.id,
                selection.blocked_busy,
                selection.blocked_unhealthy,
                selection.blocked_rejected,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:report")
        try:
            future = self.executor.submit(
                run_report_task,
                self.config,
                self.client,
                self.container_manager,
                project,
                export_yaml,
                finding,
                worker,
                cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("failed to submit report task project=%s finding=%s worker=%s", project.project.id, finding.id, worker.name)
            return False
        self.futures[future] = RunningTask(
            project_id=project.project.id,
            task_type="report",
            worker_name=worker.name,
            cancellation=cancellation,
            intent_id=finding.id,
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("dispatched report project=%s finding=%s worker=%s", project.project.id, finding.id, worker.name)
        return True

    def _dispatch_explore(self, project: ProjectDetail, export_yaml: str, intent: Task) -> bool:
        task_type = self._explore_task_type(intent.task_mode)
        if self._is_collection_task_type(task_type) and not self._collection_capacity_available():
            self._log_changed(
                f"project:{project.project.id}:collection_limit:{task_type}",
                logging.INFO,
                "skip %s project=%s intent=%s because collection_worker_limit reached running_collection_tasks=%s limit=%s",
                task_type,
                project.project.id,
                intent.id,
                self._running_collection_task_count(),
                self._current_server_settings().collection_worker_limit,
            )
            return False
        account = None
        if intent.auth_scope == "authenticated":
            if not project.accounts:
                self._log_changed(
                    f"project:{project.project.id}:accounts:missing",
                    logging.WARNING,
                    "authenticated explore cannot dispatch because project has no cookie sessions project=%s intent=%s queued_authenticated_intents=%s busy_accounts=%s total_accounts=%s",
                    project.project.id,
                    intent.id,
                    self._authenticated_queue_length(project.project.id),
                    self._busy_account_count(project.project.id),
                    len(project.accounts),
                )
                return False
            account = self._lease_account(project, intent)
            if account is None:
                self._enqueue_authenticated_waiting_intents(project.project.id, [intent])
                self._log_changed(
                    f"project:{project.project.id}:accounts:busy",
                    logging.INFO,
                    "authenticated explore waiting for cookie session project=%s intent=%s queued_authenticated_intents=%s busy_accounts=%s total_accounts=%s",
                    project.project.id,
                    intent.id,
                    self._authenticated_queue_length(project.project.id),
                    self._busy_account_count(project.project.id),
                    len(project.accounts),
                )
                return False
        selection = self._select_worker(project.project.id, task_type)
        worker = selection.worker
        if worker is None:
            if account is not None:
                self._release_account(project.project.id, account.id)
            self._log_changed(
                f"project:{project.project.id}:worker:explore",
                logging.INFO,
                "no worker available for %s project=%s intent=%s queued_authenticated_intents=%s busy_accounts=%s total_accounts=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                task_type,
                project.project.id,
                intent.id,
                self._authenticated_queue_length(project.project.id),
                self._busy_account_count(project.project.id),
                len(project.accounts),
                selection.blocked_busy,
                selection.blocked_unhealthy,
                selection.blocked_rejected,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:explore")
        claim = self.client.heartbeat(project.project.id, intent.id, worker.name)
        if claim.status_code in (403, 409):
            if account is not None:
                self._release_account(project.project.id, account.id)
            level = logging.INFO if claim.status_code == 403 else logging.WARNING
            LOG.log(
                level,
                "explore claim failed project=%s intent=%s worker=%s status=%s",
                project.project.id,
                intent.id,
                worker.name,
                claim.status_code,
            )
            return False
        if not claim.ok:
            if account is not None:
                self._release_account(project.project.id, account.id)
            LOG.warning(
                "explore claim failed project=%s intent=%s worker=%s status=%s",
                project.project.id,
                intent.id,
                worker.name,
                claim.status_code,
            )
            return False
        try:
            future = self.executor.submit(
                run_explore_task,
                self.config,
                self.client,
                self.container_manager,
                project,
                export_yaml,
                intent,
                worker,
                cancellation := TaskCancellation(),
                account,
            )
        except Exception:
            LOG.exception("failed to submit explore task project=%s intent=%s worker=%s", project.project.id, intent.id, worker.name)
            if account is not None:
                self._release_account(project.project.id, account.id)
            self._best_effort_release(project.project.id, intent.id, worker.name)
            return False
        self.futures[future] = RunningTask(
            project_id=project.project.id,
            task_type=task_type,
            worker_name=worker.name,
            cancellation=cancellation,
            intent_id=intent.id,
            account=account,
        )
        self._discard_authenticated_waiting_intent(project.project.id, intent.id)
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        if account is None:
            LOG.info("dispatched explore project=%s intent=%s worker=%s", project.project.id, intent.id, worker.name)
        else:
            LOG.info(
                "dispatched authenticated explore project=%s intent=%s worker=%s account=%s",
                project.project.id,
                intent.id,
                worker.name,
                account.id,
            )
        return True

    def _explore_task_type(self, task_mode: str) -> str:
        if task_mode == "collection":
            return "collection_explore"
        return "vulnerability_explore"

    def _select_worker(self, project_id: str, task_type: str) -> WorkerSelection:
        now = time.time()
        candidates: list[WorkerConfig] = []
        blocked_busy: list[str] = []
        blocked_unhealthy: list[str] = []
        blocked_rejected: list[str] = []
        blocked_task_type: list[str] = []
        running_counts = self._worker_counts()
        for worker in self.config.workers:
            if task_type not in worker.task_types:
                blocked_task_type.append(worker.name)
                continue
            running = running_counts.get(worker.name, 0)
            if running >= worker.max_running:
                blocked_busy.append(f"{worker.name}({running}/{worker.max_running})")
                continue
            unhealthy_until = self.worker_unhealthy_until.get(worker.name)
            if unhealthy_until is not None:
                if unhealthy_until > now:
                    blocked_unhealthy.append(f"{worker.name}({unhealthy_until - now:.1f}s)")
                    continue
                self.worker_unhealthy_until.pop(worker.name, None)
            rejection_key = (project_id, task_type, worker.name)
            rejected_until = self.worker_rejected_until.get(rejection_key)
            if rejected_until is not None:
                if rejected_until > now:
                    blocked_rejected.append(f"{worker.name}({rejected_until - now:.1f}s)")
                    continue
                self.worker_rejected_until.pop(rejection_key, None)
            candidates.append(worker)
        if not candidates:
            LOG.debug(
                "worker selection project=%s task=%s no candidates blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s blocked_task_type=%s",
                project_id,
                task_type,
                blocked_busy,
                blocked_unhealthy,
                blocked_rejected,
                blocked_task_type,
            )
            return WorkerSelection(
                worker=None,
                blocked_busy=blocked_busy,
                blocked_unhealthy=blocked_unhealthy,
                blocked_rejected=blocked_rejected,
                blocked_task_type=blocked_task_type,
            )
        ordered = choose_worker(candidates, running_counts)
        LOG.debug(
            "worker selection project=%s task=%s candidates=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s blocked_task_type=%s chosen=%s",
            project_id,
            task_type,
            [f"{worker.name}({running_counts.get(worker.name, 0)}/{worker.max_running},p{worker.priority})" for worker in candidates],
            blocked_busy,
            blocked_unhealthy,
            blocked_rejected,
            blocked_task_type,
            ordered[0].name if ordered else None,
        )
        return WorkerSelection(
            worker=ordered[0] if ordered else None,
            blocked_busy=blocked_busy,
            blocked_unhealthy=blocked_unhealthy,
            blocked_rejected=blocked_rejected,
            blocked_task_type=blocked_task_type,
        )

    def _worker_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.futures.values():
            counts[task.worker_name] = counts.get(task.worker_name, 0) + 1
        return counts

    def _is_collection_task_type(self, task_type: str) -> bool:
        return task_type in ("collection_reason", "collection_explore")

    def _running_collection_task_count(self) -> int:
        return sum(1 for task in self.futures.values() if self._is_collection_task_type(task.task_type))

    def _collection_capacity_available(self) -> bool:
        settings = self._current_server_settings()
        return self._running_collection_task_count() < settings.collection_worker_limit

    def _project_running_task_count(self, project_id: str) -> int:
        return sum(1 for task in self.futures.values() if task.project_id == project_id)

    def _project_running_task_summary(self, project_id: str) -> list[str]:
        summary: list[str] = []
        for task in self.futures.values():
            if task.project_id != project_id:
                continue
            if task.intent_id is None:
                summary.append(f"{task.task_type}:{task.worker_name}")
            else:
                summary.append(f"{task.task_type}:{task.worker_name}:{task.intent_id}")
        summary.sort()
        return summary

    def _project_running_explore_intents(self, project_id: str) -> set[str]:
        return {
            task.intent_id
            for task in self.futures.values()
            if task.project_id == project_id
            and task.task_type in ("collection_explore", "vulnerability_explore", "report")
            and task.intent_id is not None
        }

    def _project_uses_account_pool(self, project: ProjectDetail) -> bool:
        return any(
            intent.completion_time is None
            and intent.auth_scope == "authenticated"
            for intent in project.tasks
        )

    def _enqueue_current_authenticated_waiters(self, project: ProjectDetail) -> None:
        if not self._project_uses_account_pool(project):
            self.authenticated_wait_queues.pop(project.project.id, None)
            self.account_leases.pop(project.project.id, None)
            return
        if len(self.account_leases.get(project.project.id, {})) < len(project.accounts):
            return
        running_intent_ids = self._project_running_explore_intents(project.project.id)
        blocked = [
            intent
            for intent in project.tasks
            if intent.completion_time is None
            and intent.worker is None
            and intent.id not in running_intent_ids
            and intent.auth_scope == "authenticated"
        ]
        self._enqueue_authenticated_waiting_intents(project.project.id, blocked)

    def _enqueue_authenticated_waiting_intents(self, project_id: str, intents: list[Task]) -> None:
        if not intents:
            return
        queue = self.authenticated_wait_queues.setdefault(project_id, deque())
        queued = set(queue)
        for intent in sorted(intents, key=lambda item: (item.created_at, item.id)):
            if intent.id in queued:
                continue
            queue.append(intent.id)
            queued.add(intent.id)
            LOG.info(
                "queued authenticated intent project=%s intent=%s queue_length=%s",
                project_id,
                intent.id,
                len(queue),
            )

    def _next_authenticated_waiting_intent(self, project: ProjectDetail) -> Task | None:
        if not self._project_uses_account_pool(project):
            self.authenticated_wait_queues.pop(project.project.id, None)
            return None
        if len(self.account_leases.get(project.project.id, {})) >= len(project.accounts):
            return None

        queue = self.authenticated_wait_queues.get(project.project.id)
        if not queue:
            return None
        intents_by_id = {intent.id: intent for intent in project.tasks}
        while queue:
            intent_id = queue[0]
            intent = intents_by_id.get(intent_id)
            if (
                intent is not None
                and intent.completion_time is None
                and intent.worker is None
                and intent.auth_scope == "authenticated"
            ):
                LOG.debug(
                    "selected authenticated waiting intent project=%s intent=%s queue_length=%s busy_accounts=%s total_accounts=%s",
                    project.project.id,
                    intent.id,
                    len(queue),
                    self._busy_account_count(project.project.id),
                    len(project.accounts),
                )
                return intent
            LOG.debug(
                "discarding stale authenticated waiting intent project=%s intent=%s reason=%s queue_length=%s",
                project.project.id,
                intent_id,
                self._stale_authenticated_wait_reason(intent),
                len(queue),
            )
            queue.popleft()
        self.authenticated_wait_queues.pop(project.project.id, None)
        return None

    def _discard_authenticated_waiting_intent(self, project_id: str, intent_id: str) -> None:
        queue = self.authenticated_wait_queues.get(project_id)
        if not queue:
            return
        self.authenticated_wait_queues[project_id] = deque(item for item in queue if item != intent_id)
        if not self.authenticated_wait_queues[project_id]:
            self.authenticated_wait_queues.pop(project_id, None)

    def _cleanup_authenticated_wait_queues(self, summaries: list[ProjectSummary]) -> None:
        active_project_ids = {
            summary.id
            for summary in summaries
            if summary.status == "active" and summary.auth_mode != "anonymous"
        }
        for project_id in list(self.authenticated_wait_queues):
            if project_id not in active_project_ids:
                LOG.info(
                    "cleared authenticated wait queue project=%s queued_authenticated_intents=%s reason=inactive_or_anonymous",
                    project_id,
                    self._authenticated_queue_length(project_id),
                )
                self.authenticated_wait_queues.pop(project_id, None)
        for project_id in list(self.account_leases):
            if project_id not in active_project_ids:
                LOG.info(
                    "cleared authenticated account leases project=%s busy_accounts=%s reason=inactive_or_anonymous",
                    project_id,
                    self._busy_account_count(project_id),
                )
                self.account_leases.pop(project_id, None)

    def _lease_account(self, project: ProjectDetail, intent: Task):
        leased = self.account_leases.setdefault(project.project.id, {})
        for account in project.accounts:
            if account.id in leased:
                continue
            leased[account.id] = intent.id
            return account
        return None

    def _release_account(self, project_id: str, account_id: str) -> None:
        leases = self.account_leases.get(project_id)
        if not leases:
            return
        leases.pop(account_id, None)
        if not leases:
            self.account_leases.pop(project_id, None)

    def _authenticated_queue_length(self, project_id: str) -> int:
        return len(self.authenticated_wait_queues.get(project_id, ()))

    def _busy_account_count(self, project_id: str) -> int:
        return len(self.account_leases.get(project_id, {}))

    def _stale_authenticated_wait_reason(self, intent: Task | None) -> str:
        if intent is None:
            return "missing"
        if intent.completion_time is not None:
            return "concluded"
        if intent.worker is not None:
            return "claimed"
        if intent.auth_scope != "authenticated":
            return "non-authenticated"
        return "unknown"

    def _running_project_count(self, summaries: list[ProjectSummary]) -> int:
        active_ids = {summary.id for summary in summaries if summary.status == "active"}
        return len(self.runtime_project_ids & active_ids)

    def _project_open_task_count(self, project: ProjectDetail) -> int:
        return sum(1 for intent in project.tasks if intent.completion_time is None)

    def _project_open_task_count_for_mode(self, project: ProjectDetail, task_mode: TaskMode) -> int:
        return sum(
            1
            for intent in project.tasks
            if intent.completion_time is None and intent.task_mode == task_mode
        )

    def _is_initial_project(self, project: ProjectDetail) -> bool:
        return not project.facts and not project.tasks

    def _collection_warmup_complete(self, project: ProjectDetail) -> bool:
        if project.project.id in self.collection_warmup_released:
            return True
        settings = self._current_server_settings()
        complete = (
            settings.initial_collection_rounds <= 0
            or project.project.collection_explore_rounds >= settings.initial_collection_rounds
            or self._collection_warmup_converged(project)
        )
        if complete:
            self.collection_warmup_released.add(project.project.id)
        return complete

    def _collection_warmup_converged(self, project: ProjectDetail) -> bool:
        if project.project.collection_reason_rounds <= 0:
            return False
        if any(intent.completion_time is None and intent.task_mode == "collection" for intent in project.tasks):
            return False
        if self._reason_claimed(project, "collection") is not None:
            return False
        return self._reason_trigger(project, "collection") is None

    def _reason_trigger(self, project: ProjectDetail, task_mode: TaskMode) -> str | None:
        # Reason checkpoint is the last successful reason baseline. Trigger on
        # new facts, new hints, or the transition from some open tasks to none.
        changes = []
        if task_mode == "collection":
            vulnerability_trigger = self.collection_expansion_requests.get(project.project.id)
            if vulnerability_trigger is not None:
                changes.append(vulnerability_trigger)
        open_task_count = self._project_open_task_count_for_mode(project, task_mode)
        checkpoint = self.reason_checkpoints.get((project.project.id, task_mode))
        if project.project.reason_pending:
            if checkpoint is None:
                return "pending"
            changes.append("pending")
        if checkpoint is None:
            return "initial"
        if len(project.facts) > checkpoint.fact_count:
            changes.append(f"facts:{checkpoint.fact_count}->{len(project.facts)}")
        if len(project.hints) > checkpoint.hint_count:
            changes.append(f"hints:{checkpoint.hint_count}->{len(project.hints)}")
        if checkpoint.open_task_count > 0 and open_task_count == 0:
            changes.append(f"open_tasks:{checkpoint.open_task_count}->0")
        if not changes:
            return None
        return ",".join(changes)

    def _reap_futures(self) -> None:
        done = [future for future in self.futures if future.done()]
        for future in done:
            task = self.futures.pop(future)
            if task.account is not None:
                self._release_account(task.project_id, task.account.id)
                LOG.info(
                    "released authenticated account project=%s intent=%s released_account=%s queued_authenticated_intents=%s busy_accounts=%s",
                    task.project_id,
                    task.intent_id,
                    task.account.id,
                    self._authenticated_queue_length(task.project_id),
                    self._busy_account_count(task.project_id),
                )
            try:
                outcome = future.result()
                if outcome == "cancelled":
                    LOG.info(
                        "task cancelled project=%s task=%s worker=%s",
                        task.project_id,
                        task.task_type,
                        task.worker_name,
                    )
                elif outcome != "success":
                    LOG.warning(
                        "task finished project=%s task=%s worker=%s outcome=%s",
                        task.project_id,
                        task.task_type,
                        task.worker_name,
                        outcome,
                    )
                self._clear_project_log_state(task.project_id)
                if outcome == "unhealthy":
                    retry_after_seconds = UNHEALTHY_RETRY_AFTER_SECONDS
                    self.worker_unhealthy_until[task.worker_name] = time.time() + retry_after_seconds
                    LOG.info(
                        "worker marked unhealthy worker=%s retry_after=%.0fs",
                        task.worker_name,
                        retry_after_seconds,
                    )
                rejection_key = (task.project_id, task.task_type, task.worker_name)
                if outcome == "rejected":
                    retry_after_seconds = REJECTED_RETRY_AFTER_SECONDS
                    self.worker_rejected_until[rejection_key] = time.time() + retry_after_seconds
                    LOG.info(
                        "worker marked rejected project=%s task=%s worker=%s retry_after=%.0fs",
                        task.project_id,
                        task.task_type,
                        task.worker_name,
                        retry_after_seconds,
                    )
                if outcome == "success":
                    self._clear_project_failure_count(task.project_id)
                elif outcome in ("failed", "unhealthy", "rejected"):
                    self._record_project_failure(task, outcome)
                if outcome == "success" and task.task_type in ("collection_reason", "vulnerability_reason"):
                    self._update_reason_checkpoint_after_success(task)
            except Exception:
                LOG.exception("task crashed project=%s task=%s worker=%s", task.project_id, task.task_type, task.worker_name)
                self._release_crashed_task_lease(task)
                self._record_project_failure(task, "exception")

    def _clear_project_failure_count(self, project_id: str) -> None:
        self.project_failure_counts.pop(project_id, None)

    def _record_project_failure(self, task: RunningTask, outcome: str) -> None:
        failure_count = self.project_failure_counts.get(task.project_id, 0) + 1
        self.project_failure_counts[task.project_id] = failure_count
        LOG.warning(
            "project task failure recorded project=%s task=%s worker=%s outcome=%s consecutive_failures=%s",
            task.project_id,
            task.task_type,
            task.worker_name,
            outcome,
            failure_count,
        )
        if failure_count >= PROJECT_FAILURE_STOP_THRESHOLD:
            self._stop_project_after_consecutive_failures(task.project_id, failure_count)

    def _stop_project_after_consecutive_failures(self, project_id: str, failure_count: int) -> None:
        response = self.client.update_project_status(project_id, "stopped")
        if not response.ok:
            LOG.warning(
                "failed to stop project after consecutive task failures project=%s consecutive_failures=%s status=%s",
                project_id,
                failure_count,
                response.status_code,
            )
            return
        LOG.warning(
            "project stopped after consecutive task failures project=%s consecutive_failures=%s",
            project_id,
            failure_count,
        )
        self._cleanup_stopped_project_state(project_id)

    def _cleanup_stopped_project_state(self, project_id: str) -> None:
        self._clear_project_failure_count(project_id)
        self.runtime_project_ids.discard(project_id)
        self.collection_warmup_released.discard(project_id)
        self.collection_expansion_requests.pop(project_id, None)
        self.authenticated_wait_queues.pop(project_id, None)
        self.account_leases.pop(project_id, None)
        self._inactive_cleanup_done.pop(project_id, None)
        for key in list(self.reason_checkpoints):
            if key[0] == project_id:
                self.reason_checkpoints.pop(key, None)
        for key in list(self.worker_rejected_until):
            if key[0] == project_id:
                self.worker_rejected_until.pop(key, None)
        self._clear_project_log_state(project_id)
        for task in self.futures.values():
            if task.project_id == project_id and task.cancellation.cancel("stopped"):
                LOG.info(
                    "cancelling running task for stopped project project=%s task=%s worker=%s",
                    task.project_id,
                    task.task_type,
                    task.worker_name,
                )

    def _release_crashed_task_lease(self, task: RunningTask) -> None:
        if task.intent_id is not None:
            self._best_effort_release(task.project_id, task.intent_id, task.worker_name)
            return
        if task.task_type not in ("collection_reason", "vulnerability_reason"):
            return
        task_mode = task.reason_task_mode or self._task_mode_from_reason_task_type(task.task_type)
        self._best_effort_release_reason(task.project_id, task.worker_name, task_mode)

    def _update_reason_checkpoint_after_success(self, task: RunningTask) -> None:
        task_mode = task.reason_task_mode or self._task_mode_from_reason_task_type(task.task_type)
        try:
            project = self.client.get_project(task.project_id)
        except (requests.RequestException, ProtocolError) as exc:
            checkpoint = self._reason_start_checkpoint(task, task_mode)
            self.reason_checkpoints[(task.project_id, task_mode)] = checkpoint
            LOG.warning(
                "reason checkpoint refresh failed project=%s worker=%s trigger=%s error=%s fallback_facts=%s fallback_hints=%s fallback_open_tasks=%s",
                task.project_id,
                task.worker_name,
                task.reason_trigger,
                exc,
                checkpoint.fact_count,
                checkpoint.hint_count,
                checkpoint.open_task_count,
            )
            return
        checkpoint = ReasonCheckpoint(
            fact_count=len(project.facts),
            hint_count=len(project.hints),
            open_task_count=self._project_open_task_count_for_mode(project, task_mode),
            task_mode=task_mode,
        )
        self.reason_checkpoints[(task.project_id, task_mode)] = checkpoint
        if task_mode == "collection":
            self.collection_expansion_requests.pop(task.project_id, None)
        if task_mode == "vulnerability" and checkpoint.open_task_count == 0:
            self.collection_expansion_requests[task.project_id] = "vulnerability_converged"
        LOG.debug(
            "reason checkpoint updated project=%s worker=%s trigger=%s facts=%s hints=%s open_tasks=%s source=latest",
            task.project_id,
            task.worker_name,
            task.reason_trigger,
            checkpoint.fact_count,
            checkpoint.hint_count,
            checkpoint.open_task_count,
        )

    def _reason_start_checkpoint(self, task: RunningTask, task_mode: TaskMode) -> ReasonCheckpoint:
        assert task.reason_start_fact_count is not None
        assert task.reason_start_hint_count is not None
        assert task.reason_start_open_task_count is not None
        return ReasonCheckpoint(
            fact_count=task.reason_start_fact_count,
            hint_count=task.reason_start_hint_count,
            open_task_count=task.reason_start_open_task_count,
            task_mode=task_mode,
        )

    def _task_mode_from_reason_task_type(self, task_type: str) -> TaskMode:
        if task_type == "vulnerability_reason":
            return "vulnerability"
        return "collection"

    def _cleanup_completed_containers(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "completed":
                continue
            if self._inactive_cleanup_done.get(summary.id) == summary.status:
                continue
            container_name = self.container_manager.container_name(summary.id)
            if container_name in self._cleanup_pending:
                continue
            needs_cleanup = self._needs_completed_cleanup(summary.id)
            if needs_cleanup is None:
                continue
            if not needs_cleanup:
                self._inactive_cleanup_done[summary.id] = summary.status
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_completed, summary.id)
            self.cleanup_futures[future] = (container_name, summary.id, summary.status)
            self._cleanup_pending.add(container_name)

    def _cleanup_stopped_containers(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "stopped":
                continue
            if self._inactive_cleanup_done.get(summary.id) == summary.status:
                continue
            container_name = self.container_manager.container_name(summary.id)
            if container_name in self._cleanup_pending:
                continue
            needs_cleanup = self._needs_stopped_cleanup(summary.id)
            if needs_cleanup is None:
                continue
            if not needs_cleanup:
                self._inactive_cleanup_done[summary.id] = summary.status
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_stopped, summary.id)
            self.cleanup_futures[future] = (container_name, summary.id, summary.status)
            self._cleanup_pending.add(container_name)

    def _cleanup_orphan_containers(self, summaries: list[ProjectSummary]) -> None:
        known_container_names = {self.container_manager.container_name(summary.id) for summary in summaries}
        for container_name in self.container_manager.managed_container_names():
            if container_name in known_container_names:
                continue
            if container_name in self._cleanup_pending:
                continue
            needs_cleanup = self._needs_orphan_cleanup(container_name)
            if needs_cleanup is None:
                continue
            if not needs_cleanup:
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_orphan, container_name)
            self.cleanup_futures[future] = (container_name, None, "orphan")
            self._cleanup_pending.add(container_name)

    def _needs_completed_cleanup(self, project_id: str) -> bool | None:
        try:
            return self.container_manager.needs_completed_cleanup(project_id)
        except Exception:
            LOG.exception("completed container cleanup precheck failed project=%s", project_id)
            return None

    def _needs_stopped_cleanup(self, project_id: str) -> bool | None:
        try:
            return self.container_manager.needs_stopped_cleanup(project_id)
        except Exception:
            LOG.exception("stopped container cleanup precheck failed project=%s", project_id)
            return None

    def _needs_orphan_cleanup(self, container_name: str) -> bool | None:
        try:
            return self.container_manager.needs_orphan_cleanup(container_name)
        except Exception:
            LOG.exception("orphan container cleanup precheck failed container=%s", container_name)
            return None

    def _queue_container_cleanups(self, summaries: list[ProjectSummary]) -> None:
        self._cleanup_completed_containers(summaries)
        self._cleanup_stopped_containers(summaries)
        self._cleanup_orphan_containers(summaries)

    def _reap_cleanup_futures(self) -> None:
        done = [future for future in self.cleanup_futures if future.done()]
        for future in done:
            name, project_id, target_status = self.cleanup_futures.pop(future)
            self._cleanup_pending.discard(name)
            try:
                success = future.result()
                if success and project_id is not None and target_status in ("completed", "stopped"):
                    self._inactive_cleanup_done[project_id] = target_status
                elif project_id is not None:
                    self._inactive_cleanup_done.pop(project_id, None)
            except Exception:
                if project_id is not None:
                    self._inactive_cleanup_done.pop(project_id, None)
                LOG.exception("container cleanup failed container=%s", name)

    def _refresh_runtime_projects(self, summaries: list[ProjectSummary]) -> None:
        active_ids = {summary.id for summary in summaries if summary.status == "active"}
        self.runtime_project_ids.intersection_update(active_ids)
        self.collection_warmup_released.intersection_update(active_ids)
        for project_id in list(self.collection_expansion_requests):
            if project_id not in active_ids:
                self.collection_expansion_requests.pop(project_id, None)
        for project_id in list(self.project_failure_counts):
            if project_id not in active_ids:
                self.project_failure_counts.pop(project_id, None)
        inactive_status_by_id = {summary.id: summary.status for summary in summaries if summary.status != "active"}
        for project_id, status in list(self._inactive_cleanup_done.items()):
            current_status = inactive_status_by_id.get(project_id)
            if current_status != status:
                self._inactive_cleanup_done.pop(project_id, None)

    def _cancel_inactive_tasks(self, summaries: list[ProjectSummary]) -> None:
        status_by_project = {summary.id: summary.status for summary in summaries}
        for task in self.futures.values():
            status = status_by_project.get(task.project_id, "deleted")
            if status != "active" and task.cancellation.cancel(status):
                LOG.info(
                    "cancelling running task for inactive project project=%s task=%s worker=%s status=%s",
                    task.project_id,
                    task.task_type,
                    task.worker_name,
                    status,
                )

    def _initialize_reason_checkpoints(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "active":
                continue
            open_task_count = summary.working_task_count + summary.unclaimed_task_count
            if open_task_count == 0:
                continue
            project = self.client.get_project(summary.id)
            for task_mode in ("collection", "vulnerability"):
                key = (summary.id, task_mode)
                if key in self.reason_checkpoints:
                    continue
                task_mode_open_task_count = self._project_open_task_count_for_mode(project, task_mode)
                self.reason_checkpoints[key] = ReasonCheckpoint(
                    fact_count=summary.fact_count,
                    hint_count=summary.hint_count,
                    open_task_count=task_mode_open_task_count,
                    task_mode=task_mode,
                )
                LOG.debug(
                    "reason checkpoint initialized project=%s task_mode=%s facts=%s hints=%s open_tasks=%s",
                    summary.id,
                    task_mode,
                    summary.fact_count,
                    summary.hint_count,
                    task_mode_open_task_count,
                )

    def _best_effort_release(self, project_id: str, intent_id: str, worker_name: str) -> None:
        response = self.client.release(project_id, intent_id, worker_name)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("release failed project=%s intent=%s worker=%s status=%s", project_id, intent_id, worker_name, response.status_code)

    def _best_effort_release_reason(self, project_id: str, worker_name: str, task_mode: TaskMode) -> None:
        response = self.client.release_reason(project_id, worker_name, task_mode)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("reason release failed project=%s worker=%s task_mode=%s status=%s", project_id, worker_name, task_mode, response.status_code)

    def _log_changed(self, scope: str, level: int, message: str, *args: object) -> None:
        state = (level, message, args)
        if self._log_state.get(scope) == state:
            return
        self._log_state[scope] = state
        LOG.log(level, message, *args)

    def _clear_log_state(self, scope: str) -> None:
        self._log_state.pop(scope, None)

    def _clear_project_log_state(self, project_id: str) -> None:
        prefix = f"project:{project_id}:"
        for scope in list(self._log_state):
            if scope.startswith(prefix):
                self._log_state.pop(scope, None)

    def _current_server_settings(self) -> Settings:
        if self.server_settings is not None:
            return self.server_settings
        self.server_settings = self.client.get_settings()
        return self.server_settings

    def _validate_server_settings(self, settings: Settings | None = None) -> None:
        settings = settings or self.client.get_settings()
        interval = self.config.runtime.interval
        heartbeat_grace = max(interval, interval * HEARTBEAT_FAILURE_GRACE_MULTIPLIER)
        for name, value in (("task_timeout", settings.task_timeout), ("reason_timeout", settings.reason_timeout)):
            if value <= heartbeat_grace:
                raise RuntimeError(
                    f"server {name}={value}s must be greater than heartbeat grace={heartbeat_grace}s "
                    f"for dispatcher interval={interval}s"
                )
            LOG.info(
                "server setting validated %s=%ss interval=%ss",
                name,
                value,
                interval,
            )

    def _run_startup_healthchecks(self, *, show_commands: bool) -> None:
        results = run_startup_healthchecks(self.config, self.container_manager, show_commands=show_commands)
        if any(result.ok for result in results):
            return
        raise RuntimeError(format_failure_summary(results))
