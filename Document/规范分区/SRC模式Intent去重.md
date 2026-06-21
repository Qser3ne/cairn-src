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

1. 前端创建项目时提交 `project_kind`；未传 `project_kind` 时服务端默认 recon。recon 固定 `auth_mode="dual"` 并要求 accounts；vuln 继续提交项目级 `auth_mode`。新项目只创建 `origin` fact，不再接受或创建 Goal。
2. Server 将 `project_kind`、`auth_mode`、parent/snapshot、recon round 和 judge 字段写入 `projects`；recon 与 authenticated vuln 写入 `project_accounts`。
3. Scheduler 对只包含 `origin` 且无 intents 的初始项目直接 dispatch reason，不创建 bootstrap intent。
4. Prompt loader 使用 `project.project.project_kind` 选择 `default/recon` 或 `default/vuln` 子目录；`mock` prompt 组继续允许平铺目录。
5. Reason 只允许输出 `intents`、`decision="noop"` 或 `decision="no_new_high_value"`，不允许 `complete`。
6. Explore conclude 写入 fact；vuln explore 可附带 findings，recon explore 不应附带 findings；explore intent 通过 `auth_scope` 区分未登录/已登录线路。
7. Finding 的 `next_action="follow_up"` 自动创建 explore intent；`next_action="report"` 自动创建 report intent。
8. Report intent 由 report task 调度，写入 `finding_reports` 并将 finding `report_status` 改为 `drafted`。
9. Evaluate Recon 由 `POST /projects/{project_id}/recon/judgements` 创建 ephemeral judge job；任务完成后 `result_json` 保留完整 verdict/score/checklist/gaps，`GET /projects/{project_id}/recon/judgements` 返回当前 recon 项目的轻量 judgement result 列表，不返回大体积 `input_snapshot_yaml`。
10. 前端 `cairn/src/cairn/server/static/index.html` 在项目 Detail 面板展示最新 Evaluate Recon 结果和最近历史结果；`ProjectDetail` 本身仍只保留 `judge_status/judged_at` 摘要，完整 judgement 输出通过独立接口加载。

## 去重约定

- 语义去重由 `default/vuln/reason.md` 驱动，要求 AI 在创建 intent 前分析已有 facts、open intents、concluded intents 和 findings。
- recon reason 只规划 `asset_discovery`、`endpoint_sampling`、`auth_boundary_mapping`、`parameter_inventory`、`role_boundary_mapping`、`candidate_surface_collection`、`scope_clarification` 和 `noise_filtering` 八类工作；不得验证漏洞。
- recon reason 创建 intent 前必须分析 facts 覆盖的资产、端点、认证边界、参数、角色和用户数据入口，也要分析 open/concluded intents 已覆盖的目标、入口、`auth_scope`、任务类型和产生事实。
- recon reason 的 description 需要以分类标签开头并包含可去重语义，避免“继续探索”“深入分析”“进一步测试”等泛化表述。
- recon reason 需要维护 anonymous/authenticated 双线推进：初始 `origin` 图必须同时创建两条 baseline intent，后续不要求每轮都双线创建，但应避免长期只推进单条线路。
- vuln reason 只规划漏洞验证方向；一个方向已被 open/concluded intent 或 finding 覆盖时不得重复创建。
- 服务端兜底检查完全相同 `from` 集合、规范化 `description` 和 `auth_scope`，允许两条线路中存在同一来源和相似描述的分别探索。
- `from` 只要求引用现有 facts；Goal 已删除，不再有特殊禁止来源节点。
- Dispatcher 遇到重复 intent 的 409 会跳过，不把 reason 任务视为失败。
- `intent_kind="report"` 的 intent 只能绑定 `finding_id`，由 report task 消费，不走 explore prompt。
- Judge job 是 ephemeral，不参与 intent 图谱，不应作为 fact、intent 或 finding 写入 graph；UI 展示只用于辅助判断是否创建 snapshot/fork vuln。

## 验证命令

- 语法检查：`python3 -m compileall -q cairn/src/cairn cairn/tests`
- 完整测试：从 `cairn/` 目录运行 `pytest -q tests`；当前环境缺少 `uv` 时使用项目外临时 venv。本轮使用 `../.venv-test/bin/python -m pytest -q -s tests` 验证。
