---
name: production-deploy
description: Use when the user explicitly invokes $production-deploy or asks how to publish, push, deploy, or synchronize this project's carin-dev checkout to the local production checkout at /home/qser3ne/Application/carin using the repository's production deploy workflow.
---

# Production Deploy

## 标准流程

在当前项目执行生产推送时，优先使用仓库内脚本：

```bash
./scripts/deploy.sh
```

脚本的事实来源是 `scripts/deploy.sh` 和 `Document/规范分区/发布流程.md`。执行前先确认当前工作目录是 `/home/qser3ne/Application/carin-dev`，并用 `git status --short --branch` 检查开发目录状态。

默认流程由脚本完成：

1. 校验开发目录 `/home/qser3ne/Application/carin-dev` 和生产目录 `/home/qser3ne/Application/carin` 都是 Git 仓库。
2. 校验两个目录的 `origin` 一致，且都在 `main` 分支。
3. 拒绝 dev 或 prod 中未提交的 tracked 文件改动；未跟踪的 `.venv-test/` 不阻塞部署。
4. 在 dev 目录的 `cairn/` 子项目运行 `uv run --group dev pytest -s`。
5. 将 dev 的 `main` 推送到 `origin`。
6. 在 prod 目录执行 `git fetch origin` 和 `git pull --ff-only origin main`。
7. 默认备份生产 `datas/` 到 `datas.backup/<timestamp>/`。
8. 调用生产目录的 `./start.sh` 重建并重启服务。
9. 使用 `curl -f http://127.0.0.1:8000/projects` 做健康检查。

## 可选参数

仅在用户明确同意或上下文已经说明原因时使用：

```bash
./scripts/deploy.sh --skip-tests
./scripts/deploy.sh --no-backup
```

- `--skip-tests`：跳过 dev 测试。只在用户明确要求快速部署、且已接受跳过测试风险时使用。
- `--no-backup`：跳过生产 `datas/` 备份。只在确认不需要保留部署前数据快照时使用。

## 保护规则

- 不要用 `cp -r`、`rsync --delete` 或手工覆盖方式把 `carin-dev` 复制到 `carin`。
- 不要提交或覆盖生产本地的 `dispatch.yaml`、`datas/`、`.venv-test/`、`datas.backup/`。
- 不要执行 `git reset --hard`、`git checkout --` 或其他会丢弃用户改动的命令，除非用户明确要求。
- 不要在未确认用户意图时重启生产服务；执行 `./scripts/deploy.sh` 会触发生产重建和重启。
- 不要把部署脚本失败后的状态伪装成成功；必须报告失败步骤和下一步处理建议。

## 失败处理

- 如果 dev 有 unstaged 或 staged tracked 改动，停止部署，提示先提交、暂存或处理这些改动。
- 如果 prod 有 tracked 改动，停止部署，提示先确认这些生产本地修改是否应提交、暂存或放弃。
- 如果两个目录的 `origin` 不一致、分支不是 `main`、`git pull --ff-only` 失败，停止并报告实际 Git 状态。
- 如果测试失败，停止部署并报告失败命令；不要使用 `--skip-tests` 重试，除非用户明确要求。
- 如果健康检查失败，报告 `curl -f http://127.0.0.1:8000/projects` 失败，并建议检查 `docker compose ps` 和 production compose 日志。

## 收尾要求

部署相关修改完成后，按项目维护要求检查 `Document/规范分区/发布流程.md` 是否需要同步更新。最终回复要说明是否实际执行了生产部署、是否重启了生产服务、以及当前 Git 提交或部署结果。
