# SRC 工作流指南

Cairn 当前是 SRC-only 工作流。系统保留 fact/intent graph，但移除了 Standard/bootstrap 自动完成流。`completed` 只表示人工归档，不由 worker 自动判断。

## 核心角色

| 类型 | 作用 |
| --- | --- |
| `recon` project | 收集攻击面事实、功能面、认证边界、入口、参数和候选线索。 |
| `vuln` project | 从 recon snapshot 派生，用于验证漏洞、创建 findings 和报告草稿。 |
| Fact | 已确认观察，写入项目 graph。 |
| Intent | 待执行探索方向。 |
| Hint | 人类补充的策略说明。 |
| Finding | vuln 项目中的漏洞候选或已验证漏洞。 |
| Snapshot | recon graph 的冻结视图，用于 fork vuln。 |
| Ephemeral Job | judge 和 fork_seed 使用的临时任务，不直接写 graph。 |

## Recon 阶段

创建 recon 项目时：

- `project_kind` 默认为 `recon`。
- `auth_mode` 固定为 `dual`。
- 必须提供至少一个 Cookie session。
- Server 只创建初始 `origin` fact，不再创建 Goal。

Recon 的目标是建立可用于漏洞验证的上下文：

- 资产、子资产和入口。
- 页面功能、用户动作、业务流程。
- route/API 与功能点的绑定关系。
- 匿名与登录态认证边界。
- 候选攻击面和噪声排除结论。

Recon 不做漏洞验证，不创建 findings，不生成 report。Server 会拒绝 recon conclude 请求中的 `findings`，也会拒绝在 recon 项目中创建 `report` intent。

## Reason 与 Explore

`reason` 只负责规划：

- 初始 recon 必须生成 anonymous 和 authenticated 两条基线 intent。
- 后续 recon 优先规划功能地图、业务流程、route/API 绑定、认证边界和候选攻击面整理。
- `reason` 可以返回 intents、`noop` 或 `no_new_high_value`，不能返回 `complete`。

`explore` 一次执行一个 intent：

- Recon explore 写入事实。
- 功能面事实可使用 `fact_type="feature_surface"`，并写入 `title`、`summary`、`details`。
- Vuln explore 可以写 facts 和 findings。
- Authenticated explore 才会租用 Cookie session；anonymous explore 不使用登录态。

## Evaluate Recon

Evaluate Recon 会创建 `judge` ephemeral job。

Judge 的特点：

- 输入是创建 job 时捕获的 recon snapshot YAML。
- 输出 verdict、score、recommended action、checklist、blocking gaps 和 non-blocking gaps。
- 只更新 ephemeral job result 和 project 的 `judge_status/judged_at` 摘要。
- 不写 facts、intents、findings 或 reports。
- `stopped` recon 仍可执行 judge。

当前 checklist 使用以下五项：

- `scope_clarity`
- `feature_coverage`
- `feature_api_mapping_quality`
- `auth_boundary_coverage`
- `candidate_surface_quality`

Verdict 规则：

- `ready`：分数建议 `>=75`，没有 blocking gaps，且有具体候选攻击面。
- `not_ready`：仍可通过继续 recon 补齐。
- `blocked`：目标、授权、账号、范围或基础事实存在阻断。

## Snapshot 与 AI Seeded Fork

Snapshot 是 recon 与 vuln 的硬边界。Recon facts 只说明观察与候选线索，不等于漏洞结论。

默认 fork 路径：

1. 用户创建 recon snapshot。
2. 用户触发 AI seeded fork。
3. Server 创建 `fork_seed` ephemeral job。
4. Dispatcher 调度 AI fork planner 读取 snapshot graph。
5. Worker 输出 seed facts。
6. Server 原子创建 child `vuln` 项目，写入 `origin`、`recon_snapshot` reference fact 和 AI seed facts。

Seed fact 应保留：

- `title`
- `auth_scope`
- `candidate_type`
- `derived_from`
- `description`
- 可选的 `feature_summary`、`user_actions`、`routes`、`apis`、`vuln_validation_focus`、`known_constraints`、`evidence_refs`

## Vuln 阶段

Vuln 项目必须从 recon snapshot 派生。它负责：

- 验证漏洞。
- 写入漏洞验证 facts。
- 创建 findings。
- 为 finding 创建 follow-up explore intent 或 report intent。
- 调度 report task 生成 SRC 报告草稿。

Finding lifecycle 重点字段：

- `research_value`: `unknown | high | medium | low | none`
- `next_action`: `triage | follow_up | report | close`
- `followup_reason`
- `followup_intent_description`
- `followup_intent_id`
- `report_status`: `not_started | queued | drafted | submitted | closed`
- `report_intent_id`

`next_action="follow_up"` 会自动创建 explore intent。`next_action="report"` 会自动创建 report intent，并把 `report_status` 置为 `queued`。Report intent 只能存在于 vuln 项目，且必须通过 `/intents/{intent_id}/report` 写入草稿，不能通过普通 conclude 写成 fact。

## 账号池与 Auth Scope

Recon 固定 `auth_mode="dual"`，使用 intent 级 `auth_scope` 拆分：

- `anonymous`：不领取 Cookie session，不允许登录。
- `authenticated`：领取一个项目 Cookie session，并使用 session 专属隔离目录。

Vuln 使用项目级 `auth_mode`：

- `anonymous`：不能提交 accounts。
- `authenticated`：必须提交至少一个 Cookie session。

Cookie session 以明文 JSON 存储在本地 SQLite，并会出现在项目详情和 YAML export 中。只在授权范围内使用。

## 状态与删除规则

- `active`：允许 graph write。
- `stopped`：停止 graph 调度，可恢复为 `active`；仍允许已排队的 judge/fork_seed 语义。
- `completed`：人工归档，不能恢复为 `active` 或 `stopped`。
- 删除有 child vuln 的 recon 会返回 `409`。
- `/projects/{id}/complete` 和 `/projects/{id}/reopen` 保留兼容路由，但返回 `410 Gone`。
