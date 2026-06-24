# Docs Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 迁移旧中文文档目录到标准 `docs/` 文档体系，并删除旧目录。

**Architecture:** 新文档按读者角色拆分为 `user`、`architecture`、`ops`、`development` 和 `superpowers`。顶层 `README.md` 只保留公开入口和指向 `docs/` 的链接，详细说明转移到 `docs/`。旧中文文档目录作为迁移来源，在验收后删除。

**Tech Stack:** Markdown、Python `>=3.12`、uv、pytest、FastAPI、SQLite、Docker Compose。

## Global Constraints

- 默认回复和项目文档以简体中文为主，代码、命令、路径和 API 名称保持原样。
- 不复制真实 `dispatch.yaml`、Cookie、API key、账号、目标资产、OOB 资源或 worker 证据内容。
- 不修改应用代码行为。
- 删除旧中文文档目录前必须完成新 `docs/` 内容迁移。
- 验证命令统一使用 `cd cairn && uv run --group dev pytest -s`。

---

### Task 1: 创建新文档入口

**Files:**
- Create: `docs/README.md`
- Create: `docs/user/quickstart.md`
- Create: `docs/user/src-workflow.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `README.md`、旧 SRC 漏洞挖掘模式文档、旧 Recon 工作流架构文档。
- Produces: 面向用户的文档入口和 README 链接。

- [x] **Step 1: 创建 `docs/README.md`**

写入文档地图、推荐阅读路径和安全说明。

- [x] **Step 2: 创建 `docs/user/quickstart.md`**

写入依赖、配置、Docker Compose、手动启动、最小流程和测试命令。

- [x] **Step 3: 创建 `docs/user/src-workflow.md`**

写入 recon、reason/explore、judge、snapshot、AI seeded fork、vuln、账号池和状态规则。

- [x] **Step 4: 更新 `README.md`**

添加 Documentation section，并把旧中文文档链接改为 `docs/` 链接。

### Task 2: 创建架构文档

**Files:**
- Create: `docs/architecture/overview.md`
- Create: `docs/architecture/server-api.md`
- Create: `docs/architecture/data-model.md`
- Create: `docs/architecture/dispatcher.md`
- Create: `docs/architecture/worker-contracts.md`
- Create: `docs/architecture/prompts.md`

**Interfaces:**
- Consumes: `cairn/src/cairn/server/`、`cairn/src/cairn/dispatcher/`、`cairn/tests/`、旧 Server/Dispatcher/Prompt 文档。
- Produces: 开发者理解系统边界、API、数据模型、调度和 worker contract 的文档。

- [x] **Step 1: 创建架构总览**

记录技术栈、模块边界、项目类型和运行入口。

- [x] **Step 2: 创建 Server/API 文档**

记录 app 入口、router 分组、主要 endpoint 和 API 行为规则。

- [x] **Step 3: 创建数据模型文档**

记录 Pydantic 枚举、Project、Fact、Intent、Finding、Snapshot、Ephemeral Job、SQLite 表和迁移规则。

- [x] **Step 4: 创建 Dispatcher 文档**

记录配置、主循环、并发、worker 选择、账号池、容器生命周期和 task 数据流。

- [x] **Step 5: 创建 Worker contract 文档**

记录 reason、explore、judge、fork_seed、report 的 JSON 输入输出契约。

- [x] **Step 6: 创建 Prompt 文档**

记录 prompt group、目录布局、占位符、recon/vuln 策略、golden examples 和验证命令。

### Task 3: 创建运维和开发文档

**Files:**
- Create: `docs/ops/configuration-security.md`
- Create: `docs/ops/worker-container.md`
- Create: `docs/ops/deployment-release.md`
- Create: `docs/development/testing.md`

**Interfaces:**
- Consumes: `.gitignore`、`dispatch.example.yaml`、`dispatch_mock.yaml`、`container/README.md`、`container/Dockerfile`、`scripts/deploy.sh`、`.github/workflows/build-container-ghcr.yml`。
- Produces: 安全配置、worker 镜像、部署发布和测试门禁文档。

- [x] **Step 1: 创建配置安全文档**

记录可提交/不可提交配置、Cookie 风险、worker 证据和 fixture 脱敏要求。

- [x] **Step 2: 创建 worker container 文档**

记录构建、工具链、目录约定、动态容器、GHCR 和 smoke test。

- [x] **Step 3: 创建部署发布文档**

记录版本来源、本机部署脚本、worker 镜像发布、release 流程和公开前检查。

- [x] **Step 4: 创建测试文档**

记录测试命令、测试覆盖矩阵、修改类型对应验证和 release 前质量门禁。

### Task 4: 删除旧目录并验证

**Files:**
- Delete: 旧功能分区 Markdown 文件
- Delete: 旧规范分区 Markdown 文件
- Create: `docs/superpowers/specs/2026-06-24-docs-migration-design.md`
- Create: `docs/superpowers/plans/2026-06-24-docs-migration.md`

**Interfaces:**
- Consumes: 新 `docs/` 文档和旧中文文档目录。
- Produces: 单一文档体系和 Superpowers 记录。

- [x] **Step 1: 创建 Superpowers design spec**

记录迁移目标、信息来源、目录结构、迁移策略和验收标准。

- [x] **Step 2: 创建 Superpowers execution plan**

记录本迁移的执行任务和完成状态。

- [x] **Step 3: 删除旧中文文档 Markdown 文件**

用 `apply_patch` 删除旧目录下的 Markdown 文件。

- [x] **Step 4: 检查旧链接残留**

Run: `old_doc_path="Document$(printf /)" && rg "$old_doc_path" README.md docs cairn container scripts .github`

Expected: no output.

- [x] **Step 5: 运行全量测试**

Run: `uv run --group dev pytest -s` in `cairn/`

Expected: `123 passed`.

- [x] **Step 6: 检查 Git diff**

Run: `git status --short` and `git diff --stat`

Expected: README updated, `docs/` added, old Chinese document directory deleted, no application code changes.
