# SRC-only 漏洞挖掘工作流

## 目标

Cairn 现在只保留 SRC 方向工作流，项目不再区分 `standard` / `src` mode，也不再执行 bootstrap 自动完成流。用户面对的是两类项目：

- `project_kind="recon"`：默认新建项目，用于资产发现、入口整理、认证边界识别、攻击面候选收集和噪声过滤。
- `project_kind="vuln"`：漏洞验证项目，必须从 recon snapshot fork 创建，用于围绕候选攻击面持续验证可提交 SRC 漏洞。

`completed` 仅表示人工归档状态；reason、explore、judge、report worker 都不能自动完成项目。

## 用户场景

- 用户创建 recon 项目，输入 title、origin、hints，可选择 `auth_mode=anonymous|authenticated`；项目目的由 `project_kind` 固化，recon 用于信息收集，vuln 用于漏洞挖掘。
- recon 项目通过 reason 规划 recon intents，通过 explore 产出资产、入口、边界和候选攻击面 facts。
- 用户可触发 Evaluate Recon，系统创建 ephemeral judge job，评估当前 recon 是否足够 fork vuln；judge 只更新 `judge_status` 和 judgement result，不写 facts/intents/findings。
- Evaluate Recon 的最新结果会在项目 Detail 面板展示 verdict、score、recommended action、checklist、blocking gaps 和 non-blocking gaps，并保留最近历史结果，避免只看到 `judge_status` 而丢失具体判断理由。
- 用户从 recon 创建 snapshot，再从 snapshot fork 一个 vuln 项目；新 vuln 必须记录 `parent_project_id` 和 `parent_snapshot_id`。
- vuln 项目 reason 只规划非重复漏洞验证 intents；explore 可写 facts 和 findings；finding 可触发 follow-up intent 或 report intent。
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

## 输入输出

- recon 输入：title、origin、hints、`auth_mode`、可选 accounts、`recon_max_reason_rounds`。
- recon 输出：recon facts/intents、round counters、snapshot、judge job/result、children 列表。
- vuln 输入：父 recon、snapshot、title、`auth_mode`、authenticated 账号。
- vuln 输出：facts/intents、findings、follow-up intents、report intents、finding reports。
- export YAML 不再包含 `project.mode` 或 `project.bootstrap_enabled`，改为输出 `project_kind`、`auth_mode`、parent/snapshot、recon budget、finding lifecycle 和 report records。

## 验收方式

- 新建项目默认是 recon。
- UI 不再出现 Standard、SRC mode 按钮、bootstrap checkbox、Complete/Reopen modal。
- 可创建 anonymous/authenticated recon；authenticated 无 accounts 返回 422。
- 可从 recon 创建 snapshot 并 fork vuln；authenticated vuln 必须提供 accounts。
- recon 能展示 rounds、judge status、Evaluate Recon、Evaluate result、Stop Recon、Create Vulnerability Project。
- vuln 能展示 finding lifecycle、follow-up/report 状态。
- reason 输出 `complete` 被判为非法；recon stable/noop 会递增 recon round，达到上限后自动 stopped。
