# AI续接上下文

## 当前项目目录

- 后续开发工作目录固定为 `/home/qser3ne/Application/carin-dev`。
- `/home/qser3ne/Application/carin` 仅作为本次迁移来源保留，不作为后续默认开发目录。
- 当前 Git 分支为 `main`，远程 `origin` 保持为 `git@github.com:Qser3ne/cairn-src.git`。

## 本次迁移记录

- 已将 `/home/qser3ne/Application/carin` 的完整工作树和 `.git` 元数据同步到 `/home/qser3ne/Application/carin-dev`。
- 源目录中的未提交变更已带入新目录，并在新目录作为本地提交记录。
- 不创建 `/home/qser3ne/Application/carin-dev/carin` 子目录；`carin-dev` 本身就是项目根目录。

## 续接验证

- 查看状态：`git status --short --branch`
- 查看远程：`git remote -v`
- 查看最近提交：`git log --oneline --decorate -5`

## 当前功能上下文

- SRC 项目使用项目级 `auth_mode`：`anonymous` 只做未登录探索，`authenticated` 只做登录态探索。
- authenticated SRC 项目必须有 `project_accounts`，账号字段为 `label`、`username`、`password`。
- `projects.session_lock_enabled` 和 `intents.session_lock` 已从物理表、API、UI、prompt 契约中移除；只在迁移兼容代码和迁移测试中保留旧列名字符串。
- Dispatcher 使用 `authenticated_wait_queues` 和 `account_leases` 管理账号池排队；释放账号后优先补派队首 intent。
- 测试可用命令：`python3 -m compileall -q cairn/src/cairn cairn/tests`；完整 pytest 在 `/tmp/cairn-authmode-test-venv` 中已验证。

## 项目级 Skill 约束

- 项目级 Codex skill 放在 `.agent/skills/` 下。
- `.agent/skills/project-maintenance` 是项目修改后的收尾维护规则，采用被动触发。
- 每次完成项目修改后，需要阅读 `Document/` 下相关文档，检查本次修改是否影响项目内提示词，并按需同步修正文档和提示词。
- 任务完成后的最终回复需要包含 `√ <任务>`。
