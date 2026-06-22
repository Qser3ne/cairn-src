# SRC-only 漏洞挖掘工作流

## 目标

Cairn 现在只保留 SRC 方向工作流，项目不再区分 `standard` / `src` mode，也不再执行 bootstrap 自动完成流。用户面对的是两类项目：

- `project_kind="recon"`：默认新建项目，用于资产发现、入口整理、认证边界识别、攻击面候选收集和噪声过滤。
- `project_kind="vuln"`：漏洞验证项目，必须从 recon snapshot fork 创建，用于围绕候选攻击面持续验证可提交 SRC 漏洞。

`completed` 仅表示人工归档状态；reason、explore、judge、report worker 都不能自动完成项目。

## 用户场景

- 用户创建 recon 项目，输入 title、origin、hints 和至少一个 cookie session；recon 固定为 `auth_mode="dual"`，由 intent 级 `auth_scope` 分成未登录和已登录两条信息收集线路。
- recon 项目通过 reason 规划 recon intents，通过 explore 产出资产、入口、边界和候选攻击面 facts。
- 用户可触发 Evaluate Recon，系统创建 ephemeral judge job，评估当前 recon 是否足够 fork vuln；judge 只更新 `judge_status` 和 judgement result，不写 facts/intents/findings。
- Evaluate Recon 的最新结果会在项目 Detail 面板展示 verdict、score、recommended action、checklist、blocking gaps 和 non-blocking gaps，并保留最近历史结果，避免只看到 `judge_status` 而丢失具体判断理由；其中 `evidence` 和 gaps 等判断理由优先由 judge 产出简体中文，协议字段和枚举值仍保持英文。
- 用户从 recon 创建 snapshot，再触发 AI seeded fork；AI fork planner 读取 snapshot graph，生成 child vuln 的初始 seed facts。新 vuln 必须记录 `parent_project_id` 和 `parent_snapshot_id`。
- vuln 项目 reason 只规划非重复漏洞验证 intents；explore 可写 facts 和 findings；finding 可触发 follow-up intent 或 report intent。
- vuln 项目 reason 在已有 finding 后仍会检查非重复派生方向，例如新的 token 来源、接收方、接口族、最小利用条件矩阵或影响面补强；这类窄范围派生不等同于重复 finding。只有目标、入口、来源/接收方、参数矩阵和验证目的均重复时，才应返回 `noop` 或避免创建新 intent。
- vuln explore 产生 finding 时应判断是否存在明确后续派生验证方向，并通过 `followup_reason` / `followup_intent_description` 留给调度链路；若因现网回摆、token 缺失、超时 fallback 或前置条件不满足而无法完成，需要在 fact 中记录已完成矩阵、未完成矩阵和是否建议 fresh 条件下继续。
- 如果 reason 正在运行期间有 explore 写入新 fact 或用户追加 hint，系统会合并记录一次 pending reason；当前 reason 结束后立即再跑一轮 reason，用最新图快照分析期间新增信息，避免漏洞验证项目停在未分析的新 fact 上。
- report intent 由 report task 生成 `finding_reports` 草稿，并更新 finding 的 `report_status`。

## 行为边界

- `POST /projects` 默认创建 `project_kind="recon"`。
- `POST /projects` 传入旧字段 `mode`、`bootstrap_enabled` 或 `goal` 会因 `extra="forbid"` 返回 422。
- 新建 `vuln` 项目必须带 `parent_project_id` 和 `parent_snapshot_id`，且 parent 必须是 recon，snapshot 必须属于 parent。
- legacy `mode="src"` 数据库迁移为 parentless `project_kind="vuln"` 仅用于读取旧数据；新建 vuln 不允许 parent/snapshot 为空。
- 旧 `mode="standard"` 数据库启动失败，要求用户先导出或删除。
- `/projects/{id}/complete` 和 `/projects/{id}/reopen` 保留路由但返回 410 Gone。
- `PUT /projects/{id}/status` 支持 `active|stopped|completed`；`completed` 后不可恢复，只能读取、导出和改标题。
- 删除父 recon 时，如果存在 child vuln，默认返回 409。
- 新建项目 ID 使用当前已有 `proj_###` 最大编号加 1；删除当前最大编号后会复用该编号，删除中间编号不会填补空洞。
- Evaluate Recon 创建的 judge job ID 使用当前已有 `judge_###` 最大编号加 1；删除项目会级联删除其 judge jobs，因此当前最大 judge 编号也可能被后续 Evaluate 复用。

## 输入输出

- recon 输入：title、origin、hints、必填 accounts（每项表示一个 cookie session）、`recon_max_reason_rounds`；服务端固定写入 `auth_mode="dual"`。
- recon 输出：recon facts/intents、round counters、snapshot、judge job/result、children 列表。
- vuln 输入：父 recon、snapshot、title、`auth_mode`、authenticated cookie session；默认 fork 由 AI 从 snapshot 生成 seed facts，不再依赖用户手选 selected facts。
- vuln 输出：facts/intents、findings、follow-up intents、report intents、finding reports。
- export YAML 不再包含 `project.mode` 或 `project.bootstrap_enabled`，改为输出 `project_kind`、`auth_mode`、parent/snapshot、recon budget、intent `auth_scope`、finding lifecycle 和 report records。
- Worker 写入 graph 的人类可读内容采用中文优先建议：intent 说明、fact 说明和 finding 描述建议优先使用简体中文，便于国内 SRC 场景阅读；字段名、枚举值、URL、路径、参数名和技术缩写保持原样，不因英文内容判失败。

## 验收方式

- 新建项目默认是 recon。
- UI 不再出现 Standard、SRC mode 按钮、bootstrap checkbox、Complete/Reopen modal。
- 创建 recon 不带 accounts 返回 422；创建 recon 显式传 `auth_mode=anonymous|authenticated` 返回 422；创建成功的 recon 返回 `auth_mode="dual"`。
- 可从 recon 创建 snapshot 并通过 AI seeded fork 创建 vuln；authenticated vuln 必须提供 accounts。
- recon 能展示 rounds、judge status、Evaluate Recon、Evaluate result、Stop Recon、Create Vulnerability Project。
- vuln 能展示 finding lifecycle、follow-up/report 状态。
- reason 输出 `complete` 被判为非法；recon stable/noop 会递增 recon round，达到上限后自动 stopped。
- 默认 prompt 中应保留“建议优先使用简体中文”的软约束，但不应把中文输出描述成强制条件。
