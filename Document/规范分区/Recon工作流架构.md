# Recon 工作流架构

本文说明当前 `recon -> judge -> snapshot -> fork vuln -> report` 工作流的职责边界。项目仍然是 SRC-only 工作流：`recon` 只做攻击面信息收集，`vuln` 才做漏洞验证和报告产出。

## 总览

核心数据流如下：

```text
Client / UI / API
        |
        v
Cairn Server
  project graph, cookie sessions, snapshots, findings, reports, ephemeral jobs
        ^
        | HTTP API
        v
Cairn Dispatcher
  scheduling, leases, prompt rendering, worker lifecycle, output validation, writeback
        |
        | prompts in / structured JSON out
        v
Model Worker
  Claude Code / Codex / Pi / mock adapter
```

职责边界：

- Server 负责项目状态、graph 一致性、cookie session 池数据、snapshot、finding、report 和 ephemeral job 的持久化。
- Dispatcher 是 model worker 的唯一协议写入方。它调用 Cairn API claim/heartbeat/conclude/report/finish 任务，并把 worker 返回的结构化 JSON 验证后写回 server。
- Worker 只接收 prompt、在项目容器内执行任务、返回结构化 JSON。Worker 不直接调用 Cairn API，也不直接写 facts、intents、findings、reports 或 jobs。
- Prompt 定义 worker 的任务上下文、输出 schema 和安全边界；运行时写入行为仍由 dispatcher 和 server 执行。

## 项目类型：recon 与 vuln

`project_kind="recon"` 是信息收集项目，目标是为后续漏洞验证准备高质量上下文。Recon 只收集：

- 资产和子资产。
- 端点、入口、参数、功能面。
- 匿名和登录态认证边界。
- 候选攻击面和可疑但未验证的线索。
- 噪声排除结论，例如不可达入口、重复资产、无关第三方服务。

Recon 不验证漏洞，不创建 findings，不触发 report，也不把候选线索包装成已确认漏洞。

`project_kind="vuln"` 是漏洞验证项目。Vuln 必须从 recon snapshot fork 创建，用于验证 recon 候选攻击面是否构成可提交 SRC 漏洞。只有 vuln 项目负责：

- 执行漏洞验证。
- 写入漏洞相关 facts。
- 创建和更新 findings。
- 为 finding 创建 follow-up explore intent 或 report intent。
- 触发 report task 并生成 SRC 报告草稿。

`completed` 是人工归档状态，不是 worker 自动判断的完成状态。

## Recon 生命周期

Recon 生命周期按以下阶段推进：

1. 用户创建 recon 项目，提交 title、origin、hints 和至少一个 cookie session。Server 固定写入 `auth_mode="dual"`，并只创建初始 `origin` fact。
2. Dispatcher 调度 recon `reason`。Reason 读取当前 graph，提出非重复的 explore intents，通常至少覆盖 anonymous 和 authenticated 两条 baseline 线路。
3. Dispatcher 调度 recon `explore`。Explore 一次只执行一个 intent，产出已确认的攻击面 facts；不验证漏洞、不创建 findings。
4. 用户或流程触发 Evaluate Recon。Server 创建 `judge` ephemeral job，Dispatcher 调度 judge worker 评估当前 recon 是否适合 fork vuln。
5. 用户创建 recon snapshot。Snapshot 捕获 recon graph，用作 fork vuln 的边界。
6. 用户从 snapshot 触发 AI seeded fork；AI fork planner 读取 snapshot graph，生成 child vuln 的初始 seed facts。
7. Vuln 项目继续执行漏洞验证、finding 生命周期和 report 生成。

Recon 可以因为 round budget 到达上限而自动变为 `stopped`，但这只表示 recon 探索预算用尽，不表示漏洞验证完成。

## Reason / Explore / Judge 职责

`reason` 负责规划下一步，不负责执行探测：

- 在 recon 中，reason 只提出资产发现、入口采样、认证边界识别、候选攻击面整理、噪声排除相关 intents。
- 在 vuln 中，reason 可以围绕已复制的 recon facts 和已有 findings 提出验证或跟进 intents。
- Reason 可以返回 noop/stable 决策，但不能返回 `complete`。

`explore` 负责执行一个已 claim 的 intent：

- Recon explore 写入已确认的事实，例如端点存在、登录后可访问、某入口为第三方服务、某参数值得后续验证。
- Recon explore 不验证漏洞，不创建 finding，不触发 report。
- Recon explore 的 `description` 保持 dispatcher 现有 JSON 契约，但内容应使用固定小结构记录 `intent_summary`、`auth_scope`、增量观察、证据引用、边界约束、噪声排除和下一步 recon 建议；长输出只写文件路径引用。
- Vuln explore 才可以进行漏洞验证，并在确认候选具备漏洞价值时创建 findings。

`judge` 只评估 recon 是否适合 fork vuln：

- Judge 是 ephemeral job，不 claim project reason lease。
- Judge 的输入是创建 job 时捕获的 `input_snapshot_yaml`。
- Judge 输出 verdict、score、recommended action、五项 checklist、blocking gaps 和 non-blocking gaps，用于 UI 展示和 fork 决策。
- Judge 只写 ephemeral job result，并更新 project 的 `judge_status/judged_at` 摘要。
- Judge 不写入 facts、intents、findings 或 reports。
- Judge 结论是临时 readiness judgement，不代表 recon project completed。
- Recon 因 round budget 到达上限变为 `stopped` 后，仍可执行 Evaluate Recon；此时 judge 只评估创建 job 时捕获的 snapshot，不恢复或继续 graph 调度。

Judge checklist 固定包含：

- `scope_clarity`
- `asset_coverage`
- `endpoint_coverage`
- `auth_boundary_coverage`
- `candidate_surface_quality`

每项包含 `score`（0-20）和 `evidence`。总分建议为五项相加，0-100。`blocking_gaps` 和 `non_blocking_gaps` 都是可转化为后续 recon intent 的字符串数组。Judge 输出中 JSON 字段名、枚举值和 checklist key 保持英文，`evidence`、`blocking_gaps`、`non_blocking_gaps` 等 UI 可读内容建议优先使用简体中文。

Judge verdict 规则：

- `ready`：`score >= 75`，没有 blocking gaps，且至少存在可用于 vuln fork 的具体候选攻击面。
- `not_ready`：`score < 75`，但可以通过继续 recon 补齐。
- `blocked`：目标不可访问、授权/范围不清、cookie session 不可用、graph 明显缺少 origin 以外有效事实，或存在安全/合规阻断。

Judge recommended action 只使用 `create_vuln_project`、`continue_anonymous_recon`、`continue_authenticated_recon`、`clarify_scope`、`fix_account_access`、`stop_or_archive`。

## Snapshot 与 fork 边界

Snapshot 是 recon 与 vuln 的硬边界。Recon graph 中的 facts 只说明“观察到什么”和“哪些线索值得验证”，不等价于漏洞结论。

默认 AI seeded fork vuln 时：

- Parent project 必须是 recon。
- Child project 必须是 vuln。
- Child 记录 `parent_project_id` 和 `parent_snapshot_id`。
- Child 创建自己的 `origin`，并写入一条表示 recon snapshot 来源的 fact。
- AI fork planner 基于 recon snapshot 生成多个面向漏洞验证的 seed facts，并在每条 seed fact 中保留 `derived_from` 来源 fact IDs。
- Legacy/manual fork API 仍可复制 selected recon facts，但默认 UI 不再把 selected facts 作为 recon/vuln 主交接机制。

Fork 后，vuln 的 facts、findings、report records 与 parent recon 分离。Vuln 可以引用 recon snapshot 的上下文和 AI seed facts，但不能把漏洞验证结果写回 parent recon graph。

## 匿名与登录态 recon 线路

Recon 固定使用 `auth_mode="dual"`，通过 intent 级 `auth_scope` 拆分两条信息收集线路：

- `auth_scope="anonymous"`：不领取 cookie session，不允许登录，不使用任何登录态，只观察匿名可访问资产和端点。
- `auth_scope="authenticated"`：从项目 cookie session 池领取一个 session，在该 session 隔离的浏览器/session 状态中探索登录态攻击面。

Cookie session pool 由 server 存储 `{name, value}` cookie pairs，由 dispatcher 做 intent 级租约调度：

- Reason、judge、report 和 anonymous explore 不领取 cookie session。
- Authenticated explore 需要可用项目 session；没有空闲 session 时进入 FIFO 等待队列，不提前 claim intent。
- 每个 authenticated explore 只绑定一个 cookie session，任务结束、失败、取消或崩溃后释放租约。
- session 隔离是 worker session 级边界，避免不同登录态的认证状态互相污染。

## Recon 禁止事项

Recon 必须保持信息收集边界，不做漏洞验证工作：

- 不执行利用链验证或影响性证明。
- 不创建、更新或关闭 findings。
- 不创建 report intent，不生成报告草稿。
- 不把候选攻击面描述成已确认漏洞。
- 不在 judge 中补写 facts 或 intents。
- 不绕过 dispatcher 直接写 Cairn API。
- 不把 authenticated 探索得到的登录态污染到 anonymous 线路。

如果 recon 发现疑似漏洞线索，应写成候选攻击面 fact 或后续验证建议，等待 snapshot 后在 vuln 项目中验证。

## 失败与重试行为

Dispatcher 负责把 worker 失败转换为可恢复的任务状态：

- Reason 失败、拒绝或解析失败时释放 reason lease，后续调度可重试。
- Explore 在 claim 后失败或超时时释放 intent；在允许时可使用 `explore_conclude` fallback 总结已获得的事实。
- Authenticated explore 无可用 cookie session 时不 claim intent，只进入等待队列。
- Judge claim 后失败会写 ephemeral job error；stopped recon 不会取消正在运行的 judge，completed/deleted 等状态仍会取消 judge 并 fail 该 ephemeral job，避免 UI 长期显示 running。
- Report 失败时释放 report intent；成功时由 server 创建 finding report draft 并更新 finding `report_status`。

Routine heartbeat 和稳定轮询不应产生大量噪声日志；调度、claim conflict、timeout、parse failure、cookie session 租约 wait/release、judge finish/fail 等状态变化应可观测。

## Prompt 契约摘要

Prompt loader 使用：

```python
load_prompt(group, name, project_kind)
```

默认 prompt 目录布局：

```text
cairn/src/cairn/dispatcher/prompts/default/
  recon/
    reason.md
    explore.md
    explore_conclude.md
    judge.md
  vuln/
    reason.md
    explore.md
    explore_conclude.md
    report.md
```

也就是默认布局包含：

- `default/recon/{reason,explore,explore_conclude,judge}.md`
- `default/vuln/{reason,explore,explore_conclude,report}.md`

契约摘要：

- Worker 输出必须是 dispatcher 可解析的结构化 JSON。
- Reason 输出 intents 或 noop/stable 决策；recon reason intents 必须保留 recon 边界。
- Explore 输出执行结果；recon explore 只写 facts，vuln explore 可写 facts 和 findings。
- Judge 输出 ephemeral readiness judgement；包含 `verdict`、`score`、`recommended_action`、五项 checklist、blocking gaps 和 non-blocking gaps；不写 graph，不代表项目 completed。
- Report 输出 `report_markdown` 和可选 `report_json`，由 dispatcher 通过 server report API 写入 finding report draft。

更细的 dispatcher 调度、prompt 加载和写回 API 约定见 [`Dispatcher调度设计.md`](./Dispatcher调度设计.md) 与 [`Server协议规范.md`](./Server协议规范.md)。
