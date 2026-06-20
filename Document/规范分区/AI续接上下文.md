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
