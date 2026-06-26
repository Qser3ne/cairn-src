# Dispatcher 设计

Dispatcher 是 model worker 的唯一协议写入方。Worker 只接收 prompt 并返回 JSON，不直接调用 Cairn API。

## 配置入口

主配置文件为 `dispatch.yaml`，示例见 `dispatch.example.yaml`。

关键配置：

```yaml
runtime:
  interval: 3
  max_workers: 8
  max_running_projects: 3
  max_project_workers: 4
  healthcheck_timeout: 20
  worker_healthcheck: startup_only
  prompt_group: default

tasks:
  reason:
    timeout: 300
    max_intents: 2
  explore:
    timeout: 300
    conclude_timeout: 90
  report:
    timeout: 180

container:
  image: ghcr.io/oritera/cairn-worker-container:latest
  network_mode: host
  init: true
  completed_action: stop
```

Worker `task_types` 只允许：

- `collection_reason`
- `collection_explore`
- `validation_reason`
- `validation_explore`
- `report`

这五个 task types 是五类逻辑 worker 角色。示例配置使用一类 worker 对应一个 task type；实际部署可在同一 backend worker 上合并多个兼容 task types。

Server-side `/settings` 也控制运行时调度行为：

- `initial_collection_rounds` 默认 `5`。Validation/report 调度会等待完成足够多的 collection explore 执行，除非 collection 提前收敛。
- `collection_worker_limit` 默认 `1`。它全局限制正在运行的 `collection_reason` 与 `collection_explore` 总数。

配置校验规则：

- Dispatcher 配置模型对嵌套段落使用 strict parsing，未知字段会在启动配置解析阶段失败。
- 需要新增配置时，应同步更新 `dispatcher/config.py` 的 Pydantic model、`dispatch.example.yaml` 和相关测试。

## 子模块职责

| 模块 | 职责 |
| --- | --- |
| `dispatcher/config.py` | 配置模型、worker env 校验、prompt resource 校验、mock 行为解析。 |
| `dispatcher/scheduler/loop.py` | 主调度循环、并发控制、reason checkpoint、account lease、job dispatch。 |
| `dispatcher/scheduler/worker_select.py` | 按 priority、运行数和 tie-break 选择 worker。 |
| `dispatcher/tasks/` | 每种 task 的执行编排。 |
| `dispatcher/prompting.py` | 加载 prompt、格式化 graph/context、替换占位符。 |
| `dispatcher/contracts.py` | 校验 worker JSON 输出。 |
| `dispatcher/output_parser.py` | 从 stdout、Markdown fenced block 和混杂文本中提取 JSON object。 |
| `dispatcher/runtime/` | Docker container、exec process、heartbeat、startup healthcheck、cancel。 |
| `dispatcher/workers/` | WorkerDriver 抽象、registry、Claude/Codex/Pi/Mock adapters。 |
| `dispatcher/protocol/client.py` | Dispatcher 到 Server 的 HTTP API client。 |

## 主循环

`DispatcherLoop.run()` 每轮执行：

1. 启动 worker startup healthchecks。
2. 拉取 server settings，并在启动时校验 timeout settings。
3. 回收已完成 task futures。
4. 回收 container cleanup futures。
5. 拉取 projects。
6. 初始化 reason checkpoints。
7. 刷新 active runtime projects。
8. 清理 inactive 项目的 authenticated wait queues。
9. 取消 inactive/deleted projects 上的本地任务。
10. 为 stopped/completed/deleted projects 排队 container cleanup。
11. 调度 project tasks。
12. Legacy judge/fork_seed ephemeral dispatch 路径保持 no-op；retired job 不会进入新工作流调度。

## 项目调度规则

- 只为 `active` project 调度 reason、explore、report。
- 初始项目指 facts 只有 `origin` 且没有 intents。
- 初始 active project 先 dispatch `collection_reason`，按项目 `auth_mode` 生成可用的 collection baseline intents。
- Warmup 完成前不调度 report intents、validation explore 或 validation reason；collection reason/explore 仍可调度。
- Warmup 在 `collection_explore_rounds >= initial_collection_rounds` 时完成；如果至少已有一轮 collection reason 且无 open/triggerable collection work，也会提前完成。
- Authenticated explore 等待队列优先于普通未 claim intent。
- 未 claim intents 按 `report`、`validation` explore、`collection` explore 的顺序调度；同一组内选择最新 intent。
- 如果没有可调度 intent，Dispatcher 分别按 `validation`、`collection` 检查 reason trigger，并按 task mode claim reason lease。
- Validation 没有 open work 且已稳定时不会停止项目；collection 可继续扩展覆盖面。
- 不存在 bootstrap 分支。

Reason trigger 包括：

- 初始项目。
- mode-specific checkpoint 后新增 facts。
- mode-specific checkpoint 后新增 hints。
- 同一 task mode 的 open intent count 从非零变为零。
- Server 暴露 `reason_pending=true`。

`reason_checkpoints` 使用 `(project_id, task_mode)` 作为 key。Collection 和 validation reason 拥有独立 checkpoint 和独立 server lease，因此同一项目内两类 reason 工作不会通过单个项目级 lease 相互阻塞。

Reason task 只有在模型输出完成解析且关键写回成功后才会让 scheduler 更新 checkpoint。除项目已停止或租约冲突导致的 `403`、`409` 外，创建 intent 或记录 collection reason round 失败会返回 `failed`，下轮仍可重新触发，避免 checkpoint 前移造成 intent 丢失。如果写回期间同时观察到 heartbeat lease failure，即使 API 返回 `403` 或 `409`，任务也返回 `failed`，避免在租约已失效时推进 checkpoint。

## 并发限制

Dispatcher 同时受以下限制：

- `runtime.max_workers`：全局 task 并发。
- `runtime.max_running_projects`：同时运行项目数。
- `runtime.max_project_workers`：单项目 task 并发。
- `worker.max_running`：单 worker 后端并发。
- `settings.collection_worker_limit`：全局 collection reason/explore 并发。
- Cookie session 数量：authenticated explore 的实际并发上限之一。

Authenticated explore 实际并发上限：

```text
min(cookie sessions, runtime.max_project_workers, runtime.max_workers, available workers)
```

## Worker 选择

候选 worker 需要满足：

- 支持当前 task type。
- 未超过 `worker.max_running`。
- 不在临时 unhealthy window。
- 不在临时 rejected window。

排序依据：

1. `priority`，数值越小越优先。
2. 当前运行数。
3. 既有 selector tie-break 行为。

## Cookie Session 池调度

Dispatcher 维护本地结构：

```text
account_leases: project_id -> account_id -> intent_id
authenticated_wait_queues: project_id -> deque[intent_id]
```

规则：

- 只有 `auth_scope="authenticated"` 的 explore intent 领取 Cookie session。
- Reason、report 和 anonymous explore 不领取 Cookie session。
- 没有空闲 session 时，authenticated intent 进入 FIFO 等待队列，不提前 claim。
- Task 完成、失败、取消或异常时统一释放 session lease。
- Inactive project 或 anonymous project 的 session 队列和租约会被清理。

Authenticated explore prompt 会包含 session 信息和隔离目录。Anonymous explore prompt 明确禁止登录和使用 Cookie。

## 容器生命周期

`docker-compose.yaml` 只声明 `cairn-server` 和 `cairn-dispatcher`。项目 worker 容器由 Dispatcher 动态创建。

约定：

- 每个项目一个 worker 容器，名称形如 `cairn-dispatch-<project_id>`。
- 容器使用长驻 sleep 命令，Dispatcher 在其中 exec worker CLI。
- Startup healthcheck 使用临时容器，完成后删除。
- 容器创建使用配置中的 image、network mode、`init`、`cap_add`。
- `init=true` 用于回收 Playwright/Chrome 等子进程，降低 zombie 进程累积风险。
- Heartbeat 线程会把协议异常、invalid JSON 或租约冲突收敛为 task failure，并终止已 attach 的 worker 进程。
- Completed projects 按 `container.completed_action` 执行 stop/remove。
- Stopped project 会排队 container cleanup；legacy retired jobs 不作为新 workflow 的保活理由。
- Dispatcher 会清理本地 orphan worker 容器；对刚完成 task 的项目使用短暂 cooldown，避免 cleanup 与后续调度抢同一容器。

## Task 数据流摘要

| Task | Claim | Prompt 输入 | 输出写回 |
| --- | --- | --- | --- |
| `collection_reason` / `validation_reason` | per-mode project reason lease | graph YAML、fact IDs、open intents、max intents | create same-mode intents；collection 记录 reason round。 |
| `collection_explore` | intent heartbeat | graph YAML、intent、auth context | conclude fact；拒绝 findings。 |
| `validation_explore` | intent heartbeat | graph YAML、intent、auth context | conclude fact；可写 findings。 |
| `report` | report intent heartbeat | graph YAML、intent | 写 finding report draft。 |

## 可观测性

Dispatcher 日志重点记录状态变化：

- container creation/cleanup。
- worker healthcheck failures。
- task dispatch。
- parse failure。
- timeout。
- claim conflict。
- rejected worker cooldown。
- cookie session wait/release。
- retired legacy job cleanup。
- report draft writeback。

Routine heartbeat success 和稳定轮询应保持低噪声。
