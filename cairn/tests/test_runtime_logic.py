from __future__ import annotations

from dataclasses import dataclass, field
import threading

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.runtime.process import ManagedProcess


@dataclass
class FakeProcess:
    cancelled: list[str] = field(default_factory=list)
    kill_count: int = 0

    def cancel(self, reason: str) -> None:
        self.cancelled.append(reason)

    def kill(self) -> None:
        self.kill_count += 1


class FakeContainer:
    def __init__(self) -> None:
        self.client = type("Client", (), {"api": object()})()
        self.stop_count = 0
        self.archives: list[tuple[str, bytes]] = []
        self.archive_result = True

    def stop(self, timeout: int) -> None:
        assert timeout == 1
        self.stop_count += 1

    def put_archive(self, path: str, archive: bytes) -> bool:
        self.archives.append((path, archive))
        return self.archive_result


class FakeDockerContainers:
    def __init__(self) -> None:
        self.runs: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def run(self, *args, **kwargs) -> FakeContainer:
        self.runs.append((args, kwargs))
        return FakeContainer()


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeDockerContainers()


class FakeReader:
    def join(self, timeout=None) -> None:
        return None

    def is_alive(self) -> bool:
        return False


def _manager(*, completed_action: str = "stop", init: bool = True) -> ContainerManager:
    manager = ContainerManager.__new__(ContainerManager)
    manager._config = ContainerConfig(
        image="image",
        network_mode="host",
        completed_action=completed_action,
        init=init,
    )
    manager._ensure_running_locks = {}
    manager._ensure_running_locks_guard = threading.Lock()
    return manager


def test_task_cancellation_keeps_first_reason_and_cancels_late_process() -> None:
    cancellation = TaskCancellation()

    assert cancellation.cancel("project stopped")
    assert not cancellation.cancel("second reason")
    assert cancellation.reason == "project stopped"

    process = FakeProcess()
    cancellation.attach_process(process)
    assert process.cancelled == ["project stopped"]


def test_heartbeat_conflict_failure_kills_attached_process() -> None:
    process = FakeProcess()
    lease = HeartbeatLease(lambda: ApiResult(409, text="lost"), "intent", "worker", interval=60)
    lease.attach_process(process)

    lease._fail(409, "lost")

    assert lease.failure is not None
    assert lease.failure.status_code == 409
    assert process.kill_count == 1


def test_heartbeat_exception_records_failure() -> None:
    def broken_heartbeat() -> ApiResult:
        raise RuntimeError("boom")

    lease = HeartbeatLease(broken_heartbeat, "intent", "worker", interval=0)

    lease._run()

    assert lease.failure is not None
    assert lease.failure.status_code is None
    assert "boom" in lease.failure.text


def test_heartbeat_failure_before_attach_kills_late_process() -> None:
    process = FakeProcess()
    lease = HeartbeatLease(lambda: ApiResult(409, text="lost"), "intent", "worker", interval=0)

    lease._run()
    lease.attach_process(process)

    assert lease.failure is not None
    assert process.cancelled == ["heartbeat failed: lost"]


def test_managed_process_communicate_retries_kill_when_already_cancelled() -> None:
    process = ManagedProcess.__new__(ManagedProcess)
    process._reader = FakeReader()
    process._stdout = []
    process._stderr = []
    process._returncode = 137
    process._timed_out = False
    process._cancel_reason = "heartbeat failed: lost"
    process._read_error = None
    process._done = threading.Event()
    kills: list[str] = []
    process.kill = lambda: kills.append("kill")

    result = process.communicate(timeout=0)

    assert kills == ["kill"]
    assert result.cancelled
    assert result.cancel_reason == "heartbeat failed: lost"


def test_managed_process_kill_retries_transient_not_running_inspect(monkeypatch) -> None:
    class FakeApi:
        def __init__(self) -> None:
            self.inspect_calls = 0

        def exec_inspect(self, _exec_id: str) -> dict:
            self.inspect_calls += 1
            if self.inspect_calls == 1:
                return {"Running": False, "Pid": 0}
            return {"Running": True, "Pid": 1234}

    class FakeExecContainer:
        name = "container"

        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def exec_run(self, command: list[str], stdout: bool, stderr: bool):
            assert stdout is False
            assert stderr is False
            self.commands.append(command)
            return type("Result", (), {"exit_code": 0})()

    api = FakeApi()
    container = FakeExecContainer()
    process = ManagedProcess.__new__(ManagedProcess)
    process._exec_id = "exec-001"
    process._api = api
    process._container = container
    monkeypatch.setattr("cairn.dispatcher.runtime.process.time.sleep", lambda _seconds: None)

    process.kill()

    assert api.inspect_calls == 2
    assert container.commands == [["kill", "-KILL", "1234"]]


def test_container_manager_build_exec_process_wraps_command_with_timeout() -> None:
    manager = _manager()
    container = FakeContainer()
    manager._require_container = lambda _name: container

    process = manager.build_exec_process("container", {"A": "B"}, ["agent", "-p", "prompt"], timeout_seconds=300)

    assert process.command == ["timeout", "-k", "5s", "300s", "agent", "-p", "prompt"]
    assert process.env == {"A": "B"}


def test_project_container_creation_enables_docker_init() -> None:
    manager = _manager()
    client = FakeDockerClient()
    manager._client = client
    manager.inspect_state = lambda _name: None

    assert manager.ensure_running("proj_001") == "cairn-dispatch-proj_001"

    args, kwargs = client.containers.runs[0]
    assert args == ("image", ["sleep", "infinity"])
    assert kwargs["init"] is True
    assert kwargs["name"] == "cairn-dispatch-proj_001"


def test_project_container_creation_can_disable_docker_init() -> None:
    manager = _manager(init=False)
    client = FakeDockerClient()
    manager._client = client
    manager.inspect_state = lambda _name: None

    manager.ensure_running("proj_001")

    assert client.containers.runs[0][1]["init"] is False


def test_startup_container_creation_enables_docker_init() -> None:
    manager = _manager()
    client = FakeDockerClient()
    manager._client = client

    name = manager.create_startup_container()

    args, kwargs = client.containers.runs[0]
    assert args == ("image", ["sleep", "infinity"])
    assert kwargs["init"] is True
    assert kwargs["name"] == name
    assert name.startswith("cairn-startup-healthcheck-")


def test_completed_container_stop_action_only_stops_running_container() -> None:
    manager = _manager()
    container = FakeContainer()
    states = iter(["running", "exited"])
    manager.inspect_state = lambda _name: next(states)
    manager._require_container = lambda _name: container

    assert manager.cleanup_completed("proj/001")
    assert manager.container_name("proj/001") == "cairn-dispatch-proj-001"
    assert container.stop_count == 1


def test_stopped_container_cleanup_is_noop_after_container_has_already_stopped() -> None:
    manager = _manager()
    manager.inspect_state = lambda _name: "exited"

    assert manager.cleanup_stopped("proj_001")


def test_write_text_file_uses_archive_api_and_rejects_false_result() -> None:
    manager = _manager()
    container = FakeContainer()
    manager._require_container = lambda _name: container

    manager.write_text_file("container", "/tmp/graph.yaml", "facts: []\n")
    assert container.archives[0][0] == "/tmp"

    container.archive_result = False
    try:
        manager.write_text_file("container", "/tmp/graph.yaml", "facts: []\n")
    except RuntimeError as exc:
        assert "failed to write container file" in str(exc)
    else:
        raise AssertionError("expected failed put_archive result to raise")
