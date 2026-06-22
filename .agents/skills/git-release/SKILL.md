---
name: git-release
description: Use ONLY when the user explicitly invokes $git-release; performs one release commit, defaulting the version to the latest vX.Y tag plus 0.1 when the user does not specify a version, and writes the release commit description from changes since the previous version.
---

# Git Release

## 触发规则

仅在用户显式调用 `$git-release` 时使用。只要本 skill 被注入，就完成一次 release 提交；不要停留在计划或说明阶段。

## 标准流程

1. 在当前仓库执行 `git status --short --branch`，确认工作树状态。
2. 执行 `git log --oneline -10`，了解近期提交风格和当前分支历史。
3. 确定 release 版本：
   - 如果用户主动声明版本，使用用户声明的版本。
   - 如果用户未声明版本，从最新的 `vX.Y` Git tag 推断下一个版本，将 `Y` 增加 `1`，等价于版本号增加 `0.1`，例如 `v1.2` -> `v1.3`。
   - 如果没有可用的 `vX.Y` tag，使用 `v0.1`。
4. 获取相较于上个版本的更新内容：
   - 如果存在上一个版本 tag，使用 `git log <tag>..HEAD --oneline` 和必要的 `git diff <tag>..HEAD --stat` 汇总。
   - 如果不存在上一个版本 tag，使用现有提交历史和当前变更汇总首次 release 内容。
5. 检查本次 release 所需变更是否已经包含在工作树中；不要覆盖、回滚或修改用户未要求处理的改动。
6. 创建 release 提交，提交标题使用项目既有风格 `chore: release <version>`。
7. 提交正文说明相较于上个版本的更新内容，保持简洁、事实化，优先使用项目已有提交语言和风格。

## 提交规则

- release 提交必须是一次真实 Git commit。
- 如果工作树没有任何可提交变更，先检查是否应创建或更新版本记录文件；不要创建空提交，除非用户明确要求。
- 不要自动创建 tag，除非用户明确要求。
- 不要自动 push，除非用户明确要求。
- 不要 amend 既有提交，除非用户明确要求。
- 不要执行 `git reset --hard`、`git checkout --` 或其他会丢弃用户改动的命令，除非用户明确要求。

## 失败处理

- 如果存在冲突、提交失败或 hooks 失败，报告失败命令和原因，并优先修复问题后重新提交。
- 如果版本无法从用户输入或 tag 推断，停止并询问用户目标版本。
- 如果相较于上个版本没有可识别的更新内容，提交正文说明“无可识别的功能变更”，但仍需保证 release 提交基于实际文件变更。

## 收尾要求

最终回复必须说明 release 版本、是否创建提交、提交哈希以及是否创建 tag 或 push。
