# Cairn SRC
### Authorized SRC vulnerability workflow engine

This repository, `Qser3ne/cairn-src`, is a modified version of
[oritera/Cairn](https://github.com/oritera/Cairn). It is maintained by
`Qser3ne` and currently focuses on authorized Security Response Center (SRC)
vulnerability research workflows.

## What is this fork?

Cairn is a fact-graph based collaborative exploration protocol. This fork keeps
the blackboard-style fact/intent graph and narrows the current product surface
to a vuln-only SRC workflow:

- `vuln` projects start from an `origin` fact and optional account pool.
- `collection` tasks map features, APIs, authentication boundaries, and
  candidate validation seeds without writing findings.
- `validation` tasks verify vulnerability hypotheses, write evidence-backed
  findings, and queue follow-up validation or reports.
- `report` tasks draft SRC submission reports from confirmed findings.
- workers never decide that a project is complete on their own. `completed` is
  a manual archive state.

The generic Standard/bootstrap flow from upstream has been removed in this
fork. New projects grow through model-generated intents, confirmed facts, human
hints, findings, and report drafts. Legacy recon snapshots and fork jobs are
kept only for migration/history handling and are not part of the active
workflow.

## Current workflow

1. Create a `vuln` project with a title, origin, optional hints, and optional
   accounts. Projects with accounts may use `auth_mode="dual"` or
   `auth_mode="authenticated"`; projects without accounts run anonymously.
2. The dispatcher starts collection baseline work by scheduling
   `collection_reason`, which proposes anonymous and authenticated collection
   intents when the project has accounts.
3. `collection_explore` tasks collect feature, API, route, auth-boundary, and
   candidate-surface facts. These facts may include validation seed intent
   suggestions, but collection does not write findings.
4. Collection facts and validation seed intents feed `validation_reason`, which
   proposes focused vulnerability validation intents.
5. `validation_explore` tasks validate vulnerabilities, write evidence-backed
   findings, and create follow-up validation or report intents through finding
   lifecycle fields.
6. `report` tasks draft SRC submission reports from findings and update finding
   report state.

The cookie session pool is intent-scoped. Anonymous explore intents do not
lease a session. Authenticated explore intents lease one cookie session,
isolate browser and session state for that lease, and release it when the task
ends.

## Core concepts

| Concept | Meaning |
| --- | --- |
| Fact | A confirmed observation written to the project graph |
| Intent | A declared direction of exploration that has not been executed yet |
| Hint | Human guidance injected into the graph for future worker reads |
| Finding | A vulnerability candidate with lifecycle state |
| Validation seed | A collection-derived fact or intent that focuses validation work |
| Report | A drafted SRC report generated from a finding |

## Architecture

```text
Browser / API client
        |
        v
Cairn Server
  FastAPI + SQLite + static UI
  Projects / Facts / Intents / Hints / Findings / Reports / Legacy Jobs
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

## Documentation

The Chinese documentation set lives under [`docs/`](./docs/):

- [`docs/user/quickstart.md`](./docs/user/quickstart.md) for local setup and first run.
- [`docs/user/src-workflow.md`](./docs/user/src-workflow.md) for the collection/validation/report workflow.
- [`docs/architecture/`](./docs/architecture/) for server, dispatcher, data model, worker contracts, and prompt design.
- [`docs/ops/`](./docs/ops/) for configuration safety, worker containers, deployment, and release notes.
- [`docs/development/testing.md`](./docs/development/testing.md) for the test and quality gate matrix.

## Task types

| Task | Task mode | Purpose | Writes |
| --- | --- | --- | --- |
| `collection_reason` | `collection` | Propose collection or validation seed intents | Collection intents, validation seed intents, or no-op round state |
| `collection_explore` | `collection` | Collect feature/API/auth facts | Facts only |
| `validation_reason` | `validation` | Propose vulnerability validation intents | Validation intents or no-op round state |
| `validation_explore` | `validation` | Validate vulnerabilities and write findings | Facts and optional findings |
| `report` | `report` | Draft an SRC report from a finding | Finding report draft |

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

`dispatch.yaml`, local SQLite data, worker evidence, browser profiles, cookie
sessions, exported YAML, and model/API keys can contain sensitive data. Do not
commit real runtime configuration or task artifacts.

Cookie sessions are stored in the local SQLite database and can appear in
project detail/export data so workers can use them during authorized testing.
Use only sessions and targets you are allowed to test.

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
- [`docs/user/quickstart.md`](./docs/user/quickstart.md)
- [`docs/user/src-workflow.md`](./docs/user/src-workflow.md)
- [`docs/architecture/overview.md`](./docs/architecture/overview.md)
- [`docs/architecture/dispatcher.md`](./docs/architecture/dispatcher.md)
- [`docs/architecture/server-api.md`](./docs/architecture/server-api.md)
- [`container/README.md`](./container/README.md)

## Tests

Run the fast regression suite without Docker or live model endpoints:

```bash
cd cairn
uv run --group dev pytest -s
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

Do not use Cairn against systems, networks, applications, sessions, accounts,
or data without explicit permission from the owner or operator. Unauthorized
scanning, testing, exploitation, or data access may be illegal and may cause
harm.

You are responsible for how you configure and run this project, including the
targets you provide, sessions/accounts you use, worker tools you enable, and
artifacts you store. The developers and contributors do not endorse misuse and
do not accept responsibility for damage, loss, legal consequences, or policy
violations arising from unauthorized use.

## Upstream and license

This repository is a modified version of
[oritera/Cairn](https://github.com/oritera/Cairn).

The original project copyright belongs to the original authors and
contributors. Modifications in this repository are copyright `Qser3ne`.

The original project and this modified version are distributed under the GNU
Affero General Public License v3.0. See [`LICENSE`](./LICENSE) for the full
license text.
