# 快速开始

本文给出本地运行 Cairn SRC 的最短路径。更完整的工作流见 [`src-workflow.md`](./src-workflow.md)，配置安全边界见 [`../ops/configuration-security.md`](../ops/configuration-security.md)。

## 前置条件

- Linux 或 macOS。
- Python `>=3.12`。
- [`uv`](https://docs.astral.sh/uv/)。
- Docker 和 Docker Compose。
- 至少一个可用的 worker 后端配置，例如 Claude Code、Codex、Pi，或测试用 `mock`。
- 只针对已授权目标运行 SRC 流程。

## 配置 Dispatcher

复制示例配置：

```bash
cp dispatch.example.yaml dispatch.yaml
```

编辑 `dispatch.yaml`：

- `server` 指向 Cairn Server API。
- `runtime` 控制调度间隔、全局并发、项目并发、worker healthcheck 和 prompt group。
- `tasks` 控制 `reason`、`explore`、`judge`、`fork_seed`、`report` 的超时与上限。
- `container` 控制动态 worker 容器的镜像、网络、init 和完成后动作。
- `workers` 配置 worker 名称、类型、支持的任务、优先级、并发和后端环境变量。

不要把真实 `dispatch.yaml` 提交到仓库；它已在 `.gitignore` 中。

## Docker Compose 启动

默认 compose 会启动两个服务：

- `cairn-server`：FastAPI Server，映射到本机 `8000`。
- `cairn-dispatcher`：Dispatcher，挂载 Docker socket 和本地 `dispatch.yaml`。

启动：

```bash
docker compose up --build
```

打开 UI：

```text
http://127.0.0.1:8000
```

Compose 会把本地数据持久化到 `./datas/cairn/`。该目录可能包含 SQLite 数据库、Cookie session、项目导出和运行证据，不应提交。

## 手动启动

启动 Server：

```bash
uv run --project cairn cairn serve
```

启动 Dispatcher：

```bash
uv run --project cairn cairn dispatch --config dispatch.yaml
```

只运行 worker 启动健康检查：

```bash
uv run --project cairn cairn dispatch --config dispatch.yaml --startup-healthcheck-only
```

## 最小使用流程

1. 创建 `recon` 项目，提供 `title`、`origin`、必要 hints 和至少一个 Cookie session。
2. Dispatcher 调度 `reason` 生成 anonymous/authenticated 两条基线 intent。
3. Dispatcher 调度 `explore` 写入已确认 facts。
4. 在 UI 或 API 中触发 Evaluate Recon，生成 `judge` ephemeral job。
5. 创建 recon snapshot。
6. 触发 AI seeded fork，生成 child `vuln` 项目和 seed facts。
7. 在 `vuln` 项目中继续执行漏洞验证、finding 生命周期和 report 生成。

## 测试

快速回归测试：

```bash
cd cairn
uv run --group dev pytest -s
```

当前测试配置位于 `cairn/pyproject.toml`，测试目录为 `cairn/tests/`。
