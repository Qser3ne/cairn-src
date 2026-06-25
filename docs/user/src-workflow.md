# SRC 工作流指南

Cairn 当前是 vuln-only SRC 工作流。系统保留 fact/intent graph，但移除了 Standard/bootstrap 自动完成流，也不再把用户创建 recon project、recon snapshot、fork vuln、judge 或 fork_seed 作为活跃路径。`completed` 只表示人工归档，不由 worker 自动判断。

## 核心角色

| 类型 | 作用 |
| --- | --- |
| `vuln` project | 单一工作空间，从 origin 开始承载 collection、validation 和 report。 |
| `collection` task mode | 收集功能面、API、认证边界和候选验证种子。 |
| `validation` task mode | 验证漏洞假设、补充证据并创建 findings。 |
| `report` task mode | 从 finding 草拟 SRC 报告。 |
| Fact | 已确认观察，写入项目 graph。 |
| Intent | 待执行探索或报告方向。 |
| Hint | 人类补充的策略说明。 |
| Finding | 漏洞候选或已验证漏洞。 |
| Report | 基于 finding 生成的报告草稿。 |

## 创建项目

创建项目时：

- `project_kind` 固定为 `vuln`。
- 必须提供 `title` 和 `origin`。
- 可提供 hints。
- 可选提供 accounts；有账号时可使用 `auth_mode="dual"` 或 `auth_mode="authenticated"`，无账号时使用 `auth_mode="anonymous"`。
- Server 只创建初始 `origin` fact，不再创建 Goal。
- 可通过 `collection_max_reason_rounds` 控制 collection reason 的自动扩展轮次。

Cookie session 以明文 JSON 存储在本地 SQLite，并会出现在项目详情和 YAML export 中。只在授权范围内使用。

## Collection 阶段

Collection 的目标是建立可用于漏洞验证的上下文：

- 资产、子资产和入口。
- 页面功能、用户动作、业务流程。
- route/API 与功能点的绑定关系。
- 匿名与登录态认证边界。
- 候选攻击面、验证种子和噪声排除结论。

`collection_reason` 只负责规划：

- 初始项目会生成 collection baseline intents；有 accounts 的项目会区分 anonymous 和 authenticated 基线。
- 后续优先规划功能地图、业务流程、route/API 绑定、认证边界和候选攻击面整理。
- 可以创建显式 `task_mode="validation"` 的 validation seed intent，但 description 必须体现 validation focus。
- 可以返回 intents、`noop` 或 `no_new_high_value`，不能返回 `complete`。

`collection_explore` 一次执行一个 collection intent：

- 写入 facts。
- 功能面事实可使用 `fact_type="feature_surface"`，并写入 `title`、`summary`、`details`。
- 不做漏洞验证，不创建 findings，不生成 report。
- Server 会拒绝 `task_mode="collection"` conclude 请求中的 `findings`。

## Validation 阶段

Validation 从 collection facts 和 validation seed intents 继续验证漏洞。它负责：

- 验证漏洞假设。
- 写入漏洞验证 facts。
- 创建 findings。
- 为 finding 创建 follow-up validation intent 或 report intent。

`validation_reason` 读取 graph、open intents、hints 和 findings，提出新的漏洞验证 intent 或返回稳定/no-op。Validation 没有 open work 且稳定时不会自动完成项目。

`validation_explore` 可写 facts 和 findings。Finding lifecycle 重点字段：

- `research_value`: `unknown | high | medium | low | none`
- `next_action`: `triage | follow_up | report | close`
- `followup_reason`
- `followup_intent_description`
- `followup_intent_id`
- `report_status`: `not_started | queued | drafted | submitted | closed`
- `report_intent_id`

`next_action="follow_up"` 会自动创建 validation intent。`next_action="report"` 会自动创建 report intent，并把 `report_status` 置为 `queued`。

## Report 阶段

Report intent 必须绑定 finding，并使用 `task_mode="report"`。

- `report` task 消费 finding 和 graph 上下文。
- 成功后通过 `/intents/{intent_id}/report` 写入 `finding_reports`。
- Server 设置 finding `report_status="drafted"`。
- Report 不创建新 fact，也不能通过普通 `/conclude` 写成 fact。

## 账号池与 Auth Scope

项目级 `auth_mode`：

- `anonymous`：不能提交 accounts。
- `authenticated`：必须提交至少一个 Cookie session。
- `dual`：必须提交至少一个 Cookie session，collection/validation intent 可按 `auth_scope` 区分匿名和登录态。

Intent 级 `auth_scope`：

- `anonymous`：不领取 Cookie session，不允许登录。
- `authenticated`：领取一个项目 Cookie session，并使用 session 专属隔离目录。

Reason 和 report 不领取 Cookie session。

## 状态与删除规则

- `active`：允许 graph write。
- `stopped`：停止调度，可恢复为 `active`。
- `completed`：人工归档，不能恢复为 `active` 或 `stopped`。
- `/projects/{id}/complete` 和 `/projects/{id}/reopen` 保留兼容路由，但返回 `410 Gone`。

## Legacy 迁移说明

旧版本曾使用 recon project、recon snapshot、AI seeded fork、judge 和 fork_seed 建模 `recon -> snapshot -> fork vuln`。当前主流程已迁移为单一 vuln project 中的 `collection -> validation seed -> validation -> report`。

遗留 schema 字段、API 或测试名称可能仍包含 `recon`、`snapshot`、`judge` 或 `fork_seed`，用于读取旧数据、迁移旧数据库或使遗留 queued jobs 失败为 retired 状态；不要把这些路径用于新工作流。
