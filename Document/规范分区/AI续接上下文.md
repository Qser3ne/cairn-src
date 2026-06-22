# AI 续接上下文

## 当前项目目录

- 后续开发工作目录固定为 `/home/qser3ne/Application/carin-dev`。
- 当前 Git 分支为 `main`，远程 `origin` 保持为 `git@github.com:Qser3ne/cairn-src.git`。

## 本轮 SRC-only 重构状态

- 项目模型已从 `mode=standard|src` 改为 `project_kind=recon|vuln`。
- 新建项目默认 recon；新建 vuln 必须来自 recon snapshot。
- Goal 概念已删除；新项目只内置 `origin` fact，`goal` 作为 legacy 输入会返回 422，旧库启动迁移会删除 `facts.id="goal"` 和对应 intent source。
- 模式目的由 `project_kind` 固化：recon 用于信息收集，vuln 用于漏洞挖掘。
- legacy `mode="src"` 数据库迁移为 parentless vuln；legacy `mode="standard"` 数据库启动失败。
- Standard/bootstrap 自动完成流已从 dispatcher、prompt、配置和 UI 中移除。
- `/complete` 和 `/reopen` 路由只保留兼容入口并返回 410。
- `completed` 是人工归档状态；归档后不可恢复，只能读、导出和改标题。
- `session_lock_enabled` 和 `session_lock` 不重新引入；登录态并发由 intent 级 `auth_scope` 和 cookie session lease 管理。

## 当前功能上下文

- recon reason 创建 recon intents 或返回 stable/noop；stable/noop 会记录 recon reason round。
- recon explore conclude 成功后记录 recon explore round。
- recon 达到 `recon_max_reason_rounds` 后自动 `stopped` 并清空 reason lease。
- judge 是 `ephemeral_jobs`，只写 job result 和 project `judge_status/judged_at`，不写 graph。
- Project ID 和 judge ephemeral job ID 不再依赖全局 `counters` 单调递增；`next_project_id` 从当前 `projects.id` 最大 `proj_###` 计算，`next_ephemeral_job_id` 从当前 `ephemeral_jobs.id` 最大 `judge_###` 计算。删除当前最大编号后可复用该编号，中间空洞不填补；项目内 scoped IDs 仍使用 `scoped_counters`。
- stopped recon 仍允许执行 judge；dispatcher 不应因项目 stopped 取消 judge，也不应在 stopped judge 运行中执行 stopped-container cleanup。completed/deleted 等状态仍会取消 judge，并由 dispatcher 调用 ephemeral job fail 接口把 job 从 `running` 写回 `failed`。
- snapshot 只允许 recon 创建；默认 fork-vuln 走 AI seeded fork job，Dispatcher 读取 snapshot YAML 生成 seed facts，Server 创建 child vuln 并写入 parent/snapshot、`origin`、`recon_snapshot` reference fact 和 AI seed facts。旧 `/fork-vuln` copy selected facts 路径保留为 legacy/manual API。
- vuln explore 可写 findings；finding lifecycle 可自动创建 follow-up explore intent 或 report intent。
- vuln reason prompt 已补强 finding 派生策略：返回 `noop` 前必须检查全图 finding/fact 的未覆盖矩阵；同一漏洞机制下的新 token 来源、接收方、接口族、最小条件矩阵或影响面补强可以创建窄范围派生 intent，但禁止无明确新增维度的泛化 intent。
- vuln explore prompt 已要求 finding 成立时主动填写可选 follow-up 描述；无法完成的 intent 需要记录已完成矩阵、未完成矩阵和是否建议 fresh 条件复测。
- report task 写入 `finding_reports` 并更新 finding `report_status="drafted"`。
- `projects.reason_pending` 是 reason/explore 并发安全信号：reason 运行期间有新 fact/hint 写入时置为 true；当前 reason release 后 Dispatcher 会基于 pending 立即再跑一轮 reason，claim 新 reason 时清除 pending，避免新增事实被 checkpoint 吞掉。
- recon 固定 `auth_mode="dual"`，新建时必须有 `project_accounts` cookie session，reason 首轮必须创建 anonymous/authenticated 两条 baseline intent。
- vuln 继续使用项目级 `auth_mode="anonymous|authenticated"`；authenticated vuln 必须有 `project_accounts` cookie session。
- explore intent 使用 `auth_scope="anonymous|authenticated"`；scheduler 只为 authenticated explore 租 cookie session，anonymous explore 不被 session 池阻塞。
- 默认 prompt 对人类可读文本采用中文优先软建议：`intent.description`、`fact.description`、vuln `findings` 以及 judge `evidence`/gaps 建议优先简体中文；协议字段、枚举值和导出结构保持英文，不做运行时中文校验。

## 公开仓库文档状态

- 顶层 `README.md` 已按 `Qser3ne/cairn-src` 公开仓库重写，明确声明本仓库是 `oritera/Cairn` 的 modified version，由 `Qser3ne` 维护并聚焦授权 SRC 工作流。
- README 当前覆盖 recon/vuln 流程、fact-intent graph、dispatcher、worker container、cookie session 池、report 流程、配置、测试和安全免责声明。
- 原 `docs/specs` 文档已中文化并迁移到 `Document/规范分区/`：`Recon工作流架构.md`、`Dispatcher调度设计.md`、`Server协议规范.md`。`docs/` 目录已移除，README 的 See 链接已改到 `Document/规范分区`。
- README 已删除上游商业授权、PR 双授权、`personal and educational use`、上游 star history 等不适合下游公开仓库的表述。
- README 不再引用 `README/` 目录下的 banner 或运行截图图片，当前保持纯文本 Markdown 入口。
- `LICENSE` 保持标准 GNU AGPLv3 正文，不直接写入 fork 说明；版权和 attribution 说明集中放在 README。

## 续接验证

- 查看状态：`git status --short --branch`
- 语法检查：`python3 -m compileall -q cairn/src/cairn cairn/tests`
- 完整测试：`cd cairn && ../.venv-test/bin/python -m pytest -q -s tests`
- 当前环境缺少 `uv` 时，使用项目外临时 venv；本轮临时测试环境为 `/home/qser3ne/Application/carin-dev/.venv-test`，不要纳入提交。当前 pytest 全局捕获在该临时环境会触发 `FileNotFoundError`，使用 `-s` 禁用捕获。

## 项目级 Skill 约束

- 项目修改后同步维护 `Document/功能分区` 和 `Document/规范分区`。
- `.agents/skills/` 是本机项目级 skill 目录，已加入 `.gitignore`，不再纳入 Git 提交或远程仓库内容。
- 本机可继续使用 `.agents/skills/production-deploy` 指导 `scripts/deploy.sh` 发布到 `/home/qser3ne/Application/carin`，并强调不覆盖生产 `dispatch.yaml`、`datas/` 等本地状态。
- 本机可继续使用 `.agents/skills/git-release` 执行一次 release 提交；未指定版本时默认从最新 `vX.Y` tag 增加 `0.1`，提交说明按固定板块概括相较于上个版本的更新内容。
- 每次完成项目修改后，需要提交本地 Git。
- 最终回复说明测试结果、文档路径和本地提交结果。
