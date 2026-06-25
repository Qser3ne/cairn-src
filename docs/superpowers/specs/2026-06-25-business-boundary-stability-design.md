# 业务边界稳定性设计

## 背景

Cairn SRC 当前业务流是 `recon -> judge -> snapshot -> fork vuln -> finding -> report`。文档和 worker prompt 已将 `findings` 与 `report` 限定为 `vuln` 项目产物，但 Server API 仍允许 `recon` 项目在 `/intents/{id}/conclude` 中写入 findings，并由此创建 follow-up 或 report intent。这会让真实业务状态和文档边界不一致，也可能让 recon 项目进入无法被正确 report prompt 处理的状态。

## 目标

- 在 Server 层禁止 `recon` 项目写入 findings。
- 在 Server 层禁止 `recon` 项目创建或执行 report intent。
- 将 finding/report lifecycle 回归测试迁移到 `vuln` 项目，保证合法链路仍可用。
- 保留 Dispatcher 对 recon worker 输出 findings 的丢弃逻辑，Server 作为最终防线。
- 同步文档，明确 findings 和 report 只属于 vuln 验证阶段。

## 非目标

- 不在本轮实现 ephemeral job heartbeat、幂等键或重复 finish 保护。
- 不调整 UI report 可见性、pending action、轮询错误保留旧数据等交互问题。
- 不改变已有 route 名称、CLI 命令或 worker 输出 JSON contract。
- 不引入新运行时依赖。

## 设计

### Server 边界

`POST /projects/{project_id}/intents` 创建 `intent_kind="report"` 时，Server 先读取项目类型。如果项目不是 `vuln`，返回 `400`，避免 recon 项目产生 report intent。

`POST /projects/{project_id}/intents/{intent_id}/conclude` 在写 fact 前读取 project kind 和 intent kind：

- `report` intent 不允许走普通 conclude endpoint，返回 `400`，必须使用 `/report` endpoint。
- `recon` 项目请求体包含非空 `findings` 时返回 `400`。
- `vuln` 项目维持现有行为：写入 fact、创建 findings、按 `next_action` 创建 follow-up explore 或 report intent。

`POST /projects/{project_id}/intents/{intent_id}/report` 增加 project kind 校验。非 `vuln` 项目返回 `400`。

### 测试迁移

原 finding/report lifecycle 测试使用 recon 项目。迁移后测试先创建 recon parent、snapshot，再创建 child vuln，并在 child vuln 中验证：

- finding `next_action="follow_up"` 创建 explore intent。
- finding `next_action="report"` 创建 report intent。
- report conclude 后 finding `report_status` 变为 `drafted`。

新增负向测试：

- recon conclude 带 findings 返回 `400`，且不写入 finding/report intent。
- recon 创建 report intent 返回 `400`。
- report intent 不能通过普通 conclude endpoint 结束。

### 文档同步

更新架构和用户文档，明确：

- recon explore 只写 facts，不写 findings/reports。
- vuln explore 可写 findings，并按 next action 产生 follow-up 或 report intent。
- report task 只服务 vuln 项目的 report intent。

## 风险与回滚

- 这是有意的 API 行为收紧。如果外部调用方曾依赖 recon findings，会收到 `400`。该行为与现有文档和 prompt 一致。
- Dispatcher 已在 recon explore 中丢弃 model findings，因此正常 worker 链路不会受影响。
- 回滚方式是移除 Server project kind 校验并恢复旧测试，但不建议回滚，因为旧行为会继续污染业务状态。

## 验证

- `cd cairn && uv run --group dev pytest -s tests/test_server_api.py tests/test_worker_tasks.py`
- `cd cairn && uv run --group dev pytest -s`
- `python3 -m compileall -q cairn/src/cairn cairn/tests`
