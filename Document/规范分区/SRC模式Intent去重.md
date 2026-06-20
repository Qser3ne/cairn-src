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

1. 前端创建项目时提交 `mode` 和项目级 `auth_mode`；SRC 模式默认 `bootstrap_enabled=false`。
2. Server 将 `mode` 和 `auth_mode` 写入 `projects`，项目详情和列表返回这些字段；authenticated SRC 项目额外写入 `project_accounts`。
3. Scheduler 对 `mode=src` 项目跳过 bootstrap，直接 reason。
4. Reason 根据 `project.project.mode` 选择对应 prompt 子目录。
5. SRC reason 只创建新 intents，不自动 complete；intent 不携带 `auth_mode` 或会话字段。
6. Explore conclude 可附带 findings；Server 先创建 fact，再把 finding 关联到 fact 和 intent。

## 去重约定

- 语义去重由 `default/src/reason.md` 驱动，要求 AI 在创建 intent 前分析已有覆盖面。
- SRC reason 必须根据项目级 `auth_mode` 限定探索范围：anonymous 不登录，authenticated 只规划登录态攻击面。
- 服务端兜底只检查完全相同 `from` 集合和规范化 `description`，用于防误操作和明显重复。
- Dispatcher 遇到重复 intent 的 409 会跳过，不把 SRC reason 任务视为失败。
- authenticated SRC explore 由账号池调度控制并发；账号全忙时 open intent 进入本地 FIFO 队列。

## 验证命令

- 语法检查：`python3 -m compileall cairn/src/cairn cairn/tests`
- 完整测试需要项目依赖，包括 FastAPI/TestClient；可使用临时 venv 后执行 `PYTHONPATH=src pytest -q tests`。
