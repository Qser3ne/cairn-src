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
- `session_lock_enabled` 和 `session_lock` 不重新引入；账号并发由 intent 级 `auth_scope` 和 account lease 管理。

## 当前功能上下文

- recon reason 创建 recon intents 或返回 stable/noop；stable/noop 会记录 recon reason round。
- recon explore conclude 成功后记录 recon explore round。
- recon 达到 `recon_max_reason_rounds` 后自动 `stopped` 并清空 reason lease。
- judge 是 `ephemeral_jobs`，只写 job result 和 project `judge_status/judged_at`，不写 graph。
- judge 任务如果被 stopped/completed/deleted 等 inactive cancellation 中断，dispatcher 必须调用 ephemeral job fail 接口把 job 从 `running` 写回 `failed`，错误信息形如 `judge cancelled: stopped`；否则 UI/API 会长期显示 Evaluate running 但实际容器 exec 已结束。
- snapshot 只允许 recon 创建；fork-vuln 创建 child vuln 并写入 parent/snapshot、`origin`、`recon_snapshot` fact，可复制 selected facts。
- vuln explore 可写 findings；finding lifecycle 可自动创建 follow-up explore intent 或 report intent。
- report task 写入 `finding_reports` 并更新 finding `report_status="drafted"`。
- recon 固定 `auth_mode="dual"`，新建时必须有 `project_accounts`，reason 首轮必须创建 anonymous/authenticated 两条 baseline intent。
- vuln 继续使用项目级 `auth_mode="anonymous|authenticated"`；authenticated vuln 必须有 `project_accounts`。
- explore intent 使用 `auth_scope="anonymous|authenticated"`；scheduler 只为 authenticated explore 租账号，anonymous explore 不被账号池阻塞。

## 公开仓库文档状态

- 顶层 `README.md` 已按 `Qser3ne/cairn-src` 公开仓库重写，明确声明本仓库是 `oritera/Cairn` 的 modified version，由 `Qser3ne` 维护并聚焦授权 SRC 工作流。
- README 当前覆盖 recon/vuln 流程、fact-intent graph、dispatcher、worker container、账号池、report 流程、配置、测试和安全免责声明。
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
- 每次完成项目修改后，需要提交本地 Git。
- 最终回复说明测试结果、文档路径和本地提交结果。
