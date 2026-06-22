from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import threading

from pydantic import TypeAdapter
import requests
from requests.adapters import HTTPAdapter

from cairn.server.models import EphemeralJob, Intent, ProjectDetail, ProjectSummary, Settings

LOG = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    def __init__(self, message: str, status_code: int, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


@dataclass(slots=True)
class ApiResult:
    status_code: int
    data: Any | None = None
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class CairnClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._summary_adapter = TypeAdapter(list[ProjectSummary])
        self._local = threading.local()
        self._sessions: dict[int, requests.Session] = {}
        self._sessions_lock = threading.Lock()

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def list_projects(self) -> list[ProjectSummary]:
        response = self._session().get(self._url("/projects"), timeout=self._timeout)
        response.raise_for_status()
        return self._summary_adapter.validate_python(response.json())

    def get_project(self, project_id: str) -> ProjectDetail:
        response = self._session().get(self._url(f"/projects/{project_id}"), timeout=self._timeout)
        response.raise_for_status()
        return ProjectDetail.model_validate(response.json())

    def get_settings(self) -> Settings:
        response = self._session().get(self._url("/settings"), timeout=self._timeout)
        response.raise_for_status()
        return Settings.model_validate(response.json())

    def export_project(self, project_id: str) -> str:
        response = self._session().get(
            self._url(f"/projects/{project_id}/export"),
            params={"format": "yaml"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    def heartbeat(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/heartbeat",
            json={"worker": worker},
        )

    def claim_reason(self, project_id: str, worker: str, trigger: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/claim",
            json={"worker": worker, "trigger": trigger},
        )

    def reason_heartbeat(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/heartbeat",
            json={"worker": worker},
        )

    def release_reason(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/release",
            json={"worker": worker},
        )

    def release(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/release",
            json={"worker": worker},
        )

    def conclude(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        description: str,
        findings: list[dict[str, Any]] | None = None,
    ) -> ApiResult:
        body: dict[str, Any] = {"worker": worker, "description": description}
        if findings:
            body["findings"] = findings
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/conclude",
            json=body,
        )

    def record_recon_reason_round(self, project_id: str, stable: bool) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/recon/reason-round",
            json={"stable": stable},
        )

    def record_recon_explore_round(self, project_id: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/recon/explore-round",
            json={},
        )

    def create_intent(
        self,
        project_id: str,
        from_ids: list[str],
        description: str,
        creator: str,
        *,
        intent_kind: str = "explore",
        finding_id: str | None = None,
        auth_scope: str | None = None,
    ) -> ApiResult:
        body: dict[str, Any] = {
            "from": from_ids,
            "description": description,
            "creator": creator,
            "worker": None,
            "intent_kind": intent_kind,
            "finding_id": finding_id,
        }
        if auth_scope is not None:
            body["auth_scope"] = auth_scope
        return self._request_json("POST", f"/projects/{project_id}/intents", json=body)

    def conclude_report(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        report_markdown: str,
        report_json: dict[str, Any],
    ) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/report",
            json={
                "worker": worker,
                "report_markdown": report_markdown,
                "report_json": report_json,
            },
        )

    def list_queued_ephemeral_jobs(self, job_type: str = "judge") -> list[EphemeralJob]:
        response = self._session().get(
            self._url("/ephemeral-jobs/queued"),
            params={"job_type": job_type},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return [EphemeralJob.model_validate(item) for item in response.json()]

    def claim_ephemeral_job(self, job_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/ephemeral-jobs/{job_id}/claim",
            json={"worker": worker},
        )

    def finish_ephemeral_job(self, job_id: str, worker: str, result: dict[str, Any]) -> ApiResult:
        return self._request_json(
            "POST",
            f"/ephemeral-jobs/{job_id}/finish",
            json={"worker": worker, "result": result},
        )

    def finish_fork_seed_job(self, job_id: str, worker: str, seed_facts: list[dict[str, Any]]) -> ApiResult:
        return self._request_json(
            "POST",
            f"/ephemeral-jobs/{job_id}/finish-fork-seed",
            json={"worker": worker, "seed_facts": seed_facts},
        )

    def fail_ephemeral_job(self, job_id: str, worker: str, error: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/ephemeral-jobs/{job_id}/fail",
            json={"worker": worker, "error": error},
        )

    def _request_json(self, method: str, path: str, json: dict[str, Any]) -> ApiResult:
        try:
            response = self._session().request(
                method,
                self._url(path),
                json=json,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            LOG.warning("request failed method=%s path=%s error=%s", method, path, exc)
            return ApiResult(status_code=0, text=str(exc))
        data: Any | None = None
        if response.headers.get("content-type", "").startswith("application/json"):
            data = response.json()
        return ApiResult(status_code=response.status_code, data=data, text=response.text)

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session

        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64, pool_block=False)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        self._local.session = session
        with self._sessions_lock:
            self._sessions[threading.get_ident()] = session
        return session
