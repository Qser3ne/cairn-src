# Cairn SRC
### Authorized SRC recon and vulnerability workflow engine

This repository, `Qser3ne/cairn-src`, is a modified version of
[oritera/Cairn](https://github.com/oritera/Cairn). It is maintained by
`Qser3ne` and currently focuses on authorized Security Response Center (SRC)
vulnerability research workflows.

## What is this fork?

Cairn is a fact-graph based collaborative exploration protocol. This fork keeps
the blackboard-style fact/intent graph and narrows the current product surface
to an SRC-only workflow:

- `recon` projects collect attack-surface facts, authentication boundaries,
  endpoints, assets, and candidate leads.
- `vuln` projects are forked from recon snapshots and focus on validating,
  following up, and reporting vulnerabilities.
- workers never decide that a project is complete on their own. `completed` is
  a manual archive state.

The generic Standard/bootstrap flow from upstream has been removed in this
fork. New projects start from an `origin` fact and grow through model-generated
intents, confirmed facts, human hints, findings, snapshots, and report drafts.

## Current workflow

1. Create a `recon` project with a title, origin, hints, and at least one
   account. Recon always uses `auth_mode="dual"` so the system can explore both
   anonymous and authenticated attack surface.
2. The dispatcher schedules `reason` tasks to propose non-duplicate intents and
   `explore` tasks to execute one claimed intent at a time.
3. Recon `explore` tasks write confirmed facts. Recon can be evaluated by a
   `judge` task, which records an ephemeral readiness judgement without writing
   to the graph.
4. Create a recon snapshot, then fork a `vuln` project from that snapshot.
   Selected recon facts can be copied into the child project.
5. Vuln `explore` tasks can write facts and findings. Findings can create
   follow-up explore intents or report intents.
6. `report` tasks draft SRC submission reports and update finding report state.

The account pool is intent-scoped. Anonymous explore intents do not lease an
account. Authenticated explore intents lease one account, isolate browser and
session state for that account, and release the account when the task ends.

## Core concepts

| Concept | Meaning |
| --- | --- |
| Fact | A confirmed observation written to the project graph |
| Intent | A declared direction of exploration that has not been executed yet |
| Hint | Human guidance injected into the graph for future worker reads |
| Finding | A vuln-project vulnerability candidate with lifecycle state |
| Snapshot | A recon graph capture used to fork a vuln project |
| Report | A drafted SRC report generated from a finding |

## Architecture

```text
Browser / API client
        |
        v
Cairn Server
  FastAPI + SQLite + static UI
  Projects / Facts / Intents / Hints / Findings / Snapshots / Jobs
        ^
        | HTTP protocol
        v
Cairn Dispatcher
  scheduling, leases, worker selection, container lifecycle, writeback
        |
        v
Project worker containers
  Claude Code / Codex / Pi adapters
  task prompts in, structured JSON out
```

The server owns graph consistency and exposes the UI/API. The dispatcher is the
only protocol writer for model workers. Workers receive prompts, run inside
project-scoped Docker containers, and return structured output for the
dispatcher to validate and write back.

## Task types

| Task | Project kind | Purpose | Writes |
| --- | --- | --- | --- |
| `reason` | `recon`, `vuln` | Read graph state and propose useful next intents | Intents or no-op round state |
| `explore` | `recon`, `vuln` | Claim and execute one intent | Facts, optional vuln findings |
| `judge` | `recon` | Evaluate recon readiness for vuln fork | Ephemeral job result |
| `report` | `vuln` | Draft an SRC report from a finding | Finding report draft |

Supported worker backends are Claude Code, Codex, Pi, and the mock adapter used
by tests.

## Getting started

### Prerequisites

- macOS or Linux
- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker
- API access for at least one configured worker backend

### Configure the dispatcher

```bash
cp dispatch.example.yaml dispatch.yaml
```

Edit `dispatch.yaml` with your server URL, worker backend settings, model
endpoints, and API keys.

`dispatch.yaml`, local SQLite data, worker evidence, browser profiles, account
credentials, exported YAML, and model/API keys can contain sensitive data. Do
not commit real runtime configuration or task artifacts.

Accounts are stored in the local SQLite database and can appear in project
detail/export data so workers can use them during authorized testing. Use only
accounts and targets you are allowed to test.

### Docker Compose

Pull the worker image used by the default dispatcher config:

```bash
docker pull --platform=linux/amd64 ghcr.io/oritera/cairn-worker-container:latest
```

Pull the base image used by the compose build:

```bash
docker pull ghcr.io/astral-sh/uv:python3.13-trixie
```

Start the server and dispatcher:

```bash
docker compose up --build
```

This starts the Cairn server on port `8000` and starts the dispatcher after the
server health check passes. The compose setup mounts `dispatch.yaml`, connects
the dispatcher to the Docker host socket, and persists data under
`./datas/cairn/`.

Open the UI at:

```text
http://127.0.0.1:8000
```

### Manual run

Start the server:

```bash
uv run --project cairn cairn serve
```

Run the dispatcher:

```bash
uv run --project cairn cairn dispatch --config dispatch.yaml
```

Run startup health checks only:

```bash
uv run --project cairn cairn dispatch --config dispatch.yaml --startup-healthcheck-only
```

## Configuration notes

Main dispatcher configuration lives in `dispatch.yaml`.

- `server` points the dispatcher to the Cairn API server.
- `common_env` is merged into every worker process.
- `runtime` controls scheduler interval, global concurrency, per-project
  concurrency, active project limits, worker health checks, and prompt group.
- `tasks` configures timeouts and reason intent caps.
- `container` configures the project worker image, Docker network mode, init
  behavior, and completed-container action.
- `workers` configures backend type, task support, priority, concurrency, and
  backend-specific environment variables.

See:

- [`dispatch.example.yaml`](./dispatch.example.yaml)
- [`Document/规范分区/Recon工作流架构.md`](./Document/规范分区/Recon工作流架构.md)
- [`Document/规范分区/Dispatcher调度设计.md`](./Document/规范分区/Dispatcher调度设计.md)
- [`Document/规范分区/Server协议规范.md`](./Document/规范分区/Server协议规范.md)
- [`container/README.md`](./container/README.md)

## Tests

Run the fast regression suite without Docker or live model endpoints:

```bash
uv run --project cairn --group dev pytest
```

In this local workspace, a temporary test virtual environment may also be used:

```bash
cd cairn
../.venv-test/bin/python -m pytest -q -s tests
```

Do not commit `.venv-test/`.

## Security disclaimer

This project is intended only for authorized security research, SRC testing,
vulnerability validation, and related defensive workflows.

Do not use Cairn against systems, networks, applications, accounts, or data
without explicit permission from the owner or operator. Unauthorized scanning,
testing, exploitation, or data access may be illegal and may cause harm.

You are responsible for how you configure and run this project, including the
targets you provide, accounts you use, worker tools you enable, and artifacts
you store. The developers and contributors do not endorse misuse and do not
accept responsibility for damage, loss, legal consequences, or policy violations
arising from unauthorized use.

## Upstream and license

This repository is a modified version of
[oritera/Cairn](https://github.com/oritera/Cairn).

The original project copyright belongs to the original authors and
contributors. Modifications in this repository are copyright `Qser3ne`.

The original project and this modified version are distributed under the GNU
Affero General Public License v3.0. See [`LICENSE`](./LICENSE) for the full
license text.
