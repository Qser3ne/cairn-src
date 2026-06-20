# Recon/Vuln Intent 契约

## 技术入口

- 服务模型：`cairn/src/cairn/server/models.py`
- 数据库和迁移：`cairn/src/cairn/server/db.py`
- 项目 API：`cairn/src/cairn/server/routers/projects.py`
- Intent conclude、finding lifecycle 和重复兜底：`cairn/src/cairn/server/routers/intents.py`
- Dispatcher 调度：`cairn/src/cairn/dispatcher/scheduler/loop.py`
- Reason/explore/judge/report 任务：`cairn/src/cairn/dispatcher/tasks/`
- Prompt 加载：`cairn/src/cairn/dispatcher/prompting.py`
- 默认提示词目录：`cairn/src/cairn/dispatcher/prompts/default/recon` 和 `cairn/src/cairn/dispatcher/prompts/default/vuln`

## 数据流

1. 前端创建项目时提交 `project_kind` 和项目级 `auth_mode`；未传 `project_kind` 时服务端默认 recon。新项目只创建 `origin` fact，不再接受或创建 Goal。
2. Server 将 `project_kind`、`auth_mode`、parent/snapshot、recon round 和 judge 字段写入 `projects`；authenticated 项目额外写入 `project_accounts`。
3. Scheduler 对只包含 `origin` 且无 intents 的初始项目直接 dispatch reason，不创建 bootstrap intent。
4. Prompt loader 使用 `project.project.project_kind` 选择 `default/recon` 或 `default/vuln` 子目录；`mock` prompt 组继续允许平铺目录。
5. Reason 只允许输出 `intents`、`decision="noop"` 或 `decision="no_new_high_value"`，不允许 `complete`。
6. Explore conclude 写入 fact；vuln explore 可附带 findings，recon explore 不应附带 findings。
7. Finding 的 `next_action="follow_up"` 自动创建 explore intent；`next_action="report"` 自动创建 report intent。
8. Report intent 由 report task 调度，写入 `finding_reports` 并将 finding `report_status` 改为 `drafted`。

## 去重约定

- 语义去重由 `default/vuln/reason.md` 驱动，要求 AI 在创建 intent 前分析已有 facts、open intents、concluded intents 和 findings。
- recon reason 只规划资产发现、入口采样、认证边界、攻击面候选和噪声过滤；不得验证漏洞。
- vuln reason 只规划漏洞验证方向；一个方向已被 open/concluded intent 或 finding 覆盖时不得重复创建。
- 服务端兜底只检查完全相同 `from` 集合和规范化 `description`，用于防误操作和明显重复。
- `from` 只要求引用现有 facts；Goal 已删除，不再有特殊禁止来源节点。
- Dispatcher 遇到重复 intent 的 409 会跳过，不把 reason 任务视为失败。
- `intent_kind="report"` 的 intent 只能绑定 `finding_id`，由 report task 消费，不走 explore prompt。

## 验证命令

- 语法检查：`python3 -m compileall -q cairn/src/cairn cairn/tests`
- 完整测试：从 `cairn/` 目录运行 `pytest -q tests`；当前环境缺少 `uv` 时使用项目外临时 venv。本轮使用 `../.venv-test/bin/python -m pytest -q -s tests` 验证。
