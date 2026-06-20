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
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.startup_healthcheck import format_failure_summary, run_startup_healthchecks
from cairn.dispatcher.scheduler.worker_select import choose_worker
from cairn.dispatcher.tasks.explore import run_explore_task
from cairn.dispatcher.tasks.judge import run_judge_task
from cairn.dispatcher.tasks.reason import run_reason_task
from cairn.dispatcher.tasks.report import run_report_task
from cairn.server.models import Intent, ProjectDetail, ProjectSummary

LOG = logging.getLogger(__name__)
UNHEALTHY_RETRY_AFTER_SECONDS = 5
REJECTED_RETRY_AFTER_SECONDS = 5


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
        self.reason_checkpoints: dict[str, ReasonCheckpoint] = {}
        self.authenticated_wait_queues: dict[str, deque[str]] = {}
        self.account_leases: dict[str, dict[str, str]] = {}
        self.runtime_project_ids: set[str] = set()
        self.worker_unhealthy_until: dict[str, float] = {}
        self.worker_rejected_until: dict[tuple[str, str, str], float] = {}
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
                    if not self._settings_checked:
                        self._validate_server_settings()
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
                    self._dispatch_judge_jobs()
                except requests.RequestException as exc:
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
        if len(self.futures) >= self.config.runtime.max_workers:
            return
        try:
            jobs = self.client.list_queued_ephemeral_jobs("judge")
        except requests.RequestException:
            raise
        for job in jobs:
            if len(self.futures) >= self.config.runtime.max_workers:
                return
            if any(task.task_type == "judge" and task.intent_id == job.id for task in self.futures.values()):
                continue
            selection = self._select_worker(job.project_id, "judge")
            worker = selection.worker
            if worker is None:
                self._log_changed(
                    f"job:{job.id}:worker:judge",
                    logging.INFO,
                    "no worker available for judge job=%s project=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                    job.id,
                    job.project_id,
                    selection.blocked_busy,
                    selection.blocked_unhealthy,
                    selection.blocked_rejected,
                )
                continue
            try:
                future = self.executor.submit(
                    run_judge_task,
                    self.config,
                    self.client,
                    self.container_manager,
                    job,
                    worker,
                    cancellation := TaskCancellation(),
                )
            except Exception:
                LOG.exception("failed to submit judge job=%s worker=%s", job.id, worker.name)
                continue
            self.futures[future] = RunningTask(
                project_id=job.project_id,
                task_type="judge",
                worker_name=worker.name,
                cancellation=cancellation,
                intent_id=job.id,
            )
            self.runtime_project_ids.add(job.project_id)
            self._clear_log_state(f"job:{job.id}:worker:judge")
            LOG.info("dispatched judge job=%s project=%s worker=%s", job.id, job.project_id, worker.name)

    def _sync_authenticated_wait_queues(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "active" or summary.auth_mode != "authenticated":
                continue
            if summary.unclaimed_intent_count <= 0:
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
            if project.project.reason is not None:
                return False
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_reason(project, export_yaml, "initial")
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
        queued_intent = self._next_authenticated_waiting_intent(project)
        if queued_intent is not None:
            export_yaml = self.client.export_project(summary.id)
            return self._dispatch_explore(project, export_yaml, queued_intent)
        if project.project.reason is None:
            reason_trigger = self._reason_trigger(project)
            if reason_trigger is not None:
                export_yaml = self.client.export_project(summary.id)
                return self._dispatch_reason(project, export_yaml, reason_trigger)
        running_intent_ids = self._project_running_explore_intents(summary.id)
        unclaimed_intents = [
            intent
            for intent in project.intents
            if intent.to is None
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
            newest = max(unclaimed_intents, key=lambda i: i.created_at)
            export_yaml = self.client.export_project(summary.id)
            if newest.intent_kind == "report":
                return self._dispatch_report(project, export_yaml, newest)
            return self._dispatch_explore(project, export_yaml, newest)
        if project.project.reason is not None:
            self._log_changed(
                f"{skip_scope}:reason_claimed",
                logging.DEBUG,
                "skip reason project=%s because reason is already claimed by %s",
                summary.id,
                project.project.reason.worker,
            )
            return False
        self._log_changed(
            f"{skip_scope}:graph_unchanged",
            logging.DEBUG,
            "skip reason project=%s because reason state unchanged facts=%s hints=%s open_intents=%s intents=%s",
            summary.id,
            len(project.facts),
            len(project.hints),
            self._project_open_intent_count(project),
            len(project.intents),
        )
        return False

    def _dispatch_reason(self, project: ProjectDetail, export_yaml: str, trigger: str) -> bool:
        selection = self._select_worker(project.project.id, "reason")
        worker = selection.worker
        if worker is None:
            self._log_changed(
                f"project:{project.project.id}:worker:reason",
                logging.INFO,
                "no worker available for reason project=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                project.project.id,
                selection.blocked_busy,
                selection.blocked_unhealthy,
                selection.blocked_rejected,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:reason")
        claim = self.client.claim_reason(project.project.id, worker.name, trigger)
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
                cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("failed to submit reason task project=%s worker=%s", project.project.id, worker.name)
            self._best_effort_release_reason(project.project.id, worker.name)
            return False
        self.futures[future] = RunningTask(
            project_id=project.project.id,
            task_type="reason",
            worker_name=worker.name,
            cancellation=cancellation,
            intent_id=None,
            fact_count=len(project.facts),
            hint_count=len(project.hints),
            open_intent_count=self._project_open_intent_count(project),
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("dispatched reason project=%s worker=%s trigger=%s", project.project.id, worker.name, trigger)
        return True

    def _dispatch_report(self, project: ProjectDetail, export_yaml: str, intent: Intent) -> bool:
        selection = self._select_worker(project.project.id, "report")
        worker = selection.worker
        if worker is None:
            self._log_changed(
                f"project:{project.project.id}:worker:report",
                logging.INFO,
                "no worker available for report project=%s intent=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                project.project.id,
                intent.id,
                selection.blocked_busy,
                selection.blocked_unhealthy,
                selection.blocked_rejected,
            )
            return False
        self._clear_log_state(f"project:{project.project.id}:worker:report")
        claim = self.client.heartbeat(project.project.id, intent.id, worker.name)
        if claim.status_code in (403, 409):
            LOG.info(
                "report claim failed project=%s intent=%s worker=%s status=%s",
                project.project.id,
                intent.id,
                worker.name,
                claim.status_code,
            )
            return False
        if not claim.ok:
            LOG.warning(
                "report claim failed project=%s intent=%s worker=%s status=%s",
                project.project.id,
                intent.id,
                worker.name,
                claim.status_code,
            )
            return False
        try:
            future = self.executor.submit(
                run_report_task,
                self.config,
                self.client,
                self.container_manager,
                project,
                export_yaml,
                intent,
                worker,
                cancellation := TaskCancellation(),
            )
        except Exception:
            LOG.exception("failed to submit report task project=%s intent=%s worker=%s", project.project.id, intent.id, worker.name)
            self._best_effort_release(project.project.id, intent.id, worker.name)
            return False
        self.futures[future] = RunningTask(
            project_id=project.project.id,
            task_type="report",
            worker_name=worker.name,
            cancellation=cancellation,
            intent_id=intent.id,
        )
        self.runtime_project_ids.add(project.project.id)
        self._clear_project_log_state(project.project.id)
        LOG.info("dispatched report project=%s intent=%s worker=%s", project.project.id, intent.id, worker.name)
        return True

    def _dispatch_explore(self, project: ProjectDetail, export_yaml: str, intent: Intent) -> bool:
        account = None
        if project.project.auth_mode == "authenticated":
            if not project.accounts:
                self._log_changed(
                    f"project:{project.project.id}:accounts:missing",
                    logging.WARNING,
                    "authenticated explore cannot dispatch because project has no accounts project=%s intent=%s",
                    project.project.id,
                    intent.id,
                )
                return False
            account = self._lease_account(project, intent)
            if account is None:
                self._enqueue_authenticated_waiting_intents(project.project.id, [intent])
                self._log_changed(
                    f"project:{project.project.id}:accounts:busy",
                    logging.INFO,
                    "authenticated explore waiting for account project=%s intent=%s busy_accounts=%s total_accounts=%s",
                    project.project.id,
                    intent.id,
                    len(self.account_leases.get(project.project.id, {})),
                    len(project.accounts),
                )
                return False
        selection = self._select_worker(project.project.id, "explore")
        worker = selection.worker
        if worker is None:
            if account is not None:
                self._release_account(project.project.id, account.id)
            self._log_changed(
                f"project:{project.project.id}:worker:explore",
                logging.INFO,
                "no worker available for explore project=%s intent=%s blocked_busy=%s blocked_unhealthy=%s blocked_rejected=%s",
                project.project.id,
                intent.id,
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
            task_type="explore",
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
            unhealthy_until = self.worker_unhealthy_until.get(worker.name, 0)
            if unhealthy_until > now:
                blocked_unhealthy.append(f"{worker.name}({unhealthy_until - now:.1f}s)")
                continue
            rejected_until = self.worker_rejected_until.get((project_id, task_type, worker.name), 0)
            if rejected_until > now:
                blocked_rejected.append(f"{worker.name}({rejected_until - now:.1f}s)")
                continue
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
            if task.project_id == project_id and task.task_type in ("explore", "report") and task.intent_id is not None
        }

    def _project_uses_account_pool(self, project: ProjectDetail) -> bool:
        return project.project.auth_mode == "authenticated"

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
            for intent in project.intents
            if intent.to is None
            and intent.worker is None
            and intent.id not in running_intent_ids
            and intent.intent_kind == "explore"
        ]
        self._enqueue_authenticated_waiting_intents(project.project.id, blocked)

    def _enqueue_authenticated_waiting_intents(self, project_id: str, intents: list[Intent]) -> None:
        if not intents:
            return
        queue = self.authenticated_wait_queues.setdefault(project_id, deque())
        queued = set(queue)
        for intent in sorted(intents, key=lambda item: (item.created_at, item.id)):
            if intent.id in queued:
                continue
            queue.append(intent.id)
            queued.add(intent.id)

    def _next_authenticated_waiting_intent(self, project: ProjectDetail) -> Intent | None:
        if not self._project_uses_account_pool(project):
            self.authenticated_wait_queues.pop(project.project.id, None)
            return None
        if len(self.account_leases.get(project.project.id, {})) >= len(project.accounts):
            return None

        queue = self.authenticated_wait_queues.get(project.project.id)
        if not queue:
            return None
        intents_by_id = {intent.id: intent for intent in project.intents}
        while queue:
            intent = intents_by_id.get(queue[0])
            if (
                intent is not None
                and intent.to is None
                and intent.worker is None
                and intent.intent_kind == "explore"
            ):
                return intent
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
        active_authenticated_ids = {
            summary.id
            for summary in summaries
            if summary.status == "active" and summary.auth_mode == "authenticated"
        }
        for project_id in list(self.authenticated_wait_queues):
            if project_id not in active_authenticated_ids:
                self.authenticated_wait_queues.pop(project_id, None)
        for project_id in list(self.account_leases):
            if project_id not in active_authenticated_ids:
                self.account_leases.pop(project_id, None)

    def _lease_account(self, project: ProjectDetail, intent: Intent):
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

    def _running_project_count(self, summaries: list[ProjectSummary]) -> int:
        active_ids = {summary.id for summary in summaries if summary.status == "active"}
        return len(self.runtime_project_ids & active_ids)

    def _project_open_intent_count(self, project: ProjectDetail) -> int:
        return sum(1 for intent in project.intents if intent.to is None)

    def _is_initial_project(self, project: ProjectDetail) -> bool:
        fact_ids = {fact.id for fact in project.facts}
        return fact_ids == {"origin", "goal"} and len(project.facts) == 2 and not project.intents

    def _reason_trigger(self, project: ProjectDetail) -> str | None:
        open_intent_count = self._project_open_intent_count(project)
        checkpoint = self.reason_checkpoints.get(project.project.id)
        if checkpoint is None:
            return "initial"
        changes: list[str] = []
        if len(project.facts) > checkpoint.fact_count:
            changes.append(f"facts:{checkpoint.fact_count}->{len(project.facts)}")
        if len(project.hints) > checkpoint.hint_count:
            changes.append(f"hints:{checkpoint.hint_count}->{len(project.hints)}")
        if checkpoint.open_intent_count > 0 and open_intent_count == 0:
            changes.append(f"open_intents:{checkpoint.open_intent_count}->0")
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
                    "released authenticated account project=%s intent=%s account=%s",
                    task.project_id,
                    task.intent_id,
                    task.account.id,
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
                else:
                    self.worker_unhealthy_until.pop(task.worker_name, None)
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
                else:
                    self.worker_rejected_until.pop(rejection_key, None)
                if outcome == "success" and task.task_type == "reason":
                    assert task.fact_count is not None
                    assert task.hint_count is not None
                    assert task.open_intent_count is not None
                    self.reason_checkpoints[task.project_id] = ReasonCheckpoint(
                        fact_count=task.fact_count,
                        hint_count=task.hint_count,
                        open_intent_count=task.open_intent_count,
                    )
                    LOG.debug(
                        "reason checkpoint updated project=%s facts=%s hints=%s open_intents=%s",
                        task.project_id,
                        task.fact_count,
                        task.hint_count,
                        task.open_intent_count,
                    )
            except Exception:
                LOG.exception("task crashed project=%s task=%s worker=%s", task.project_id, task.task_type, task.worker_name)

    def _cleanup_completed_containers(self, summaries: list[ProjectSummary]) -> None:
        for summary in summaries:
            if summary.status != "completed":
                continue
            if self._inactive_cleanup_done.get(summary.id) == summary.status:
                continue
            container_name = self.container_manager.container_name(summary.id)
            if container_name in self._cleanup_pending:
                continue
            if not self.container_manager.needs_completed_cleanup(summary.id):
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
            if not self.container_manager.needs_stopped_cleanup(summary.id):
                self._inactive_cleanup_done[summary.id] = summary.status
                continue
            future = self.cleanup_executor.submit(self.container_manager.cleanup_stopped, summary.id)
            self.cleanup_futures[future] = (container_name, summary.id, summary.status)
            self._cleanup_pending.add(container_name)

    def _queue_container_cleanups(self, summaries: list[ProjectSummary]) -> None:
        self._cleanup_completed_containers(summaries)
        self._cleanup_stopped_containers(summaries)

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
            if summary.id in self.reason_checkpoints:
                continue
            open_intent_count = summary.working_intent_count + summary.unclaimed_intent_count
            if open_intent_count == 0:
                continue
            self.reason_checkpoints[summary.id] = ReasonCheckpoint(
                fact_count=summary.fact_count,
                hint_count=summary.hint_count,
                open_intent_count=open_intent_count,
            )
            LOG.debug(
                "reason checkpoint initialized project=%s facts=%s hints=%s open_intents=%s",
                summary.id,
                summary.fact_count,
                summary.hint_count,
                open_intent_count,
            )

    def _best_effort_release(self, project_id: str, intent_id: str, worker_name: str) -> None:
        response = self.client.release(project_id, intent_id, worker_name)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("release failed project=%s intent=%s worker=%s status=%s", project_id, intent_id, worker_name, response.status_code)

    def _best_effort_release_reason(self, project_id: str, worker_name: str) -> None:
        response = self.client.release_reason(project_id, worker_name)
        if not response.ok and response.status_code not in (403, 409):
            LOG.warning("reason release failed project=%s worker=%s status=%s", project_id, worker_name, response.status_code)

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

    def _validate_server_settings(self) -> None:
        settings = self.client.get_settings()
        interval = self.config.runtime.interval
        for name, value in (("intent_timeout", settings.intent_timeout), ("reason_timeout", settings.reason_timeout)):
            if value <= interval:
                raise RuntimeError(
                    f"server {name}={value}s must be greater than dispatcher interval={interval}s"
                )
            if value < interval * 2:
                LOG.warning(
                    "server %s is tight %s=%ss interval=%ss; heartbeat slack is only %ss",
                    name,
                    name,
                    value,
                    interval,
                    value - interval,
                )
                continue
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
