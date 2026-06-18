# SRC模式Intent去重

## 技术入口

- 服务模型：`cairn/src/cairn/server/models.py`
- 数据库和迁移：`cairn/src/cairn/server/db.py`
- 项目 API：`cairn/src/cairn/server/routers/projects.py`
- Intent conclude 和重复兜底：`cairn/src/cairn/server/routers/intents.py`
- Dispatcher reason/explore：`cairn/src/cairn/dispatcher/tasks/reason.py`、`cairn/src/cairn/dispatcher/tasks/explore.py`
- Prompt 加载：`cairn/src/cairn/dispatcher/prompting.py`
- 默认提示词目录：`cairn/src/cairn/dispatcher/prompts/default/standard` 和 `cairn/src/cairn/dispatcher/prompts/default/src`

## 数据流

1. 前端创建项目时提交 `mode`；SRC 模式默认 `bootstrap_enabled=false`。
2. Server 将 `mode` 和 `session_lock_enabled` 写入 `projects`，项目详情和列表返回这些字段。
3. Scheduler 对 `mode=src` 项目跳过 bootstrap，直接 reason。
4. Reason 根据 `project.project.mode` 选择对应 prompt 子目录。
5. SRC reason 只创建新 intents，不自动 complete；每个新 intent 必须输出 `session_lock`。
6. Explore conclude 可附带 findings；Server 先创建 fact，再把 finding 关联到 fact 和 intent。

## 去重约定

- 语义去重由 `default/src/reason.md` 驱动，要求 AI 在创建 intent 前分析已有覆盖面。
- 服务端兜底只检查完全相同 `from` 集合和规范化 `description`，用于防误操作和明显重复。
- Dispatcher 遇到重复 intent 的 409 会跳过，不把 SRC reason 任务视为失败。
- 涉及登录态、Cookie、Token、账号状态、认证链路或共享会话的 SRC intent 应设置 `session_lock=true`，由 dispatcher 做同项目互斥和本地排队。

## 验证命令

- 语法检查：`python3 -m compileall cairn/src/cairn cairn/tests`
- 完整测试需要项目依赖，包括 FastAPI/TestClient；当前系统 Python 缺少 FastAPI 时无法运行 pytest。
