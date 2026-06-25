# 架构总览

Cairn SRC 是基于 fact/intent graph 的授权 SRC 工作流引擎。当前 fork 聚焦单一 `vuln` project 内的 `collection -> validation seed -> validation -> report`，移除了上游通用 Standard/bootstrap 流程。Legacy recon snapshot/fork 仅作为迁移历史处理。

## 技术栈

| 分类 | 技术 |
| --- | --- |
| 语言 | Python `>=3.12` |
| 包管理/构建 | `uv`、`uv_build` |
| Web/API | FastAPI、Uvicorn、Pydantic |
| CLI | Click |
| 数据库 | SQLite，标准库 `sqlite3` |
| 配置/协议 | YAML、JSON、PyYAML、Requests |
| 调度/容器 | Docker SDK、Docker Compose、动态 Docker worker 容器 |
| 静态 UI | HTML、Alpine.js、Tailwind CSS、Cytoscape 与布局插件 |
| Worker 后端 | Claude Code、Codex、Pi、Mock |
| 测试 | pytest、httpx |

包入口定义在 `cairn/pyproject.toml`：

```toml
[project.scripts]
cairn = "cairn.cli:main"
```

## 总体数据流

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
  Claude Code / Codex / Pi / mock adapters
  task prompts in, structured JSON out
```

## 模块边界

| 模块 | 路径 | 职责 |
| --- | --- | --- |
| CLI | `cairn/src/cairn/cli.py` | 提供 `cairn serve` 和 `cairn dispatch`。 |
| Server | `cairn/src/cairn/server/` | FastAPI app、SQLite schema、业务模型、API routers。 |
| Dispatcher | `cairn/src/cairn/dispatcher/` | 调度循环、prompt 渲染、worker adapter、容器与 writeback。 |
| Static UI | `cairn/src/cairn/server/static/` | 单页 UI 和本地 vendor 资源。 |
| Worker 镜像 | `container/` | Kali worker 环境、工具链、容器内运行指令。 |
| Tests | `cairn/tests/` | API、调度、契约、迁移、prompt、runtime 与 mock E2E 测试。 |

## Server 职责

Server 是 graph 和运行状态的事实来源：

- 保存 projects、facts、intents、hints、findings、reports、cookie session pools，以及 legacy snapshot/job 迁移数据。
- 维护 SQLite schema 和 legacy 迁移。
- 提供 UI 和 HTTP API。
- 校验 project kind、auth mode、intent 去重、finding lifecycle 和 report 写入规则。
- 不运行模型推理，不直接执行 worker 任务。

## Dispatcher 职责

Dispatcher 是 model worker 的唯一协议写入方：

- 拉取 Server 项目和 legacy queued ephemeral jobs。
- 选择 worker 后端。
- 管理 project worker 容器。
- 渲染 prompt，注入 graph YAML 和 auth context。
- 启动 worker CLI 并解析输出。
- 使用 contract 校验 JSON。
- 通过 Server API 写回 facts、intents、findings 和 reports；legacy retired jobs 只写失败结果。

Worker 不直接写 Cairn API，也不直接修改 graph。

## 项目类型

| 类型 | 作用 | 写入边界 |
| --- | --- | --- |
| `vuln` | 承载 collection、validation 和 report 的 SRC 工作空间。 | Collection 写 facts；validation 写 facts/findings；report 写 report drafts。 |

`completed` 是人工归档状态，不代表 worker 自动完成。

## 任务类型

| Task | Task mode | 目的 | 写入 |
| --- | --- | --- | --- |
| `collection_reason` | `collection` | 读取 graph，规划 collection 或 validation seed intents。 | Intents 或 collection round state。 |
| `collection_explore` | `collection` | 执行 collection intent，建立功能/API/auth facts。 | Facts only。 |
| `validation_reason` | `validation` | 读取 collection facts、validation seeds 和 findings，规划验证方向。 | Validation intents 或 stable/no-op。 |
| `validation_explore` | `validation` | 执行漏洞验证 intent。 | Facts，可附带 findings。 |
| `report` | `report` | 从 finding 生成 SRC 报告草稿。 | Finding report draft。 |

Server 会强制执行 task mode 边界：`collection` conclude 请求不能包含 `findings`，`validation` 才能写 findings，`report` intent 必须通过 report endpoint 写入草稿。

## 运行入口

- Docker Compose：`docker compose up --build`。
- Server：`uv run --project cairn cairn serve`。
- Dispatcher：`uv run --project cairn cairn dispatch --config dispatch.yaml`。
- Worker healthcheck：`uv run --project cairn cairn dispatch --config dispatch.yaml --startup-healthcheck-only`。
- 测试：`cd cairn && uv run --group dev pytest -s`。

## 关键约束

- 新建项目固定为 `vuln`。
- `auth_mode="anonymous"` 项目不能包含 accounts；`authenticated` 和 `dual` 项目必须包含至少一个 Cookie session。
- Legacy parent/snapshot 字段只用于迁移或历史数据读取，不是新项目主流程要求。
- Standard mode、bootstrap task、Goal fact、自动 complete/reopen 已移除。
- `/projects/{id}/complete` 和 `/projects/{id}/reopen` 只保留兼容路由，返回 `410 Gone`。
