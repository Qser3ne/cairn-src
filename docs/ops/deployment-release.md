# 部署与发布

本文记录本机生产部署、版本同步和公开发布检查。执行发布前先完成 [`../development/testing.md`](../development/testing.md) 中的质量门禁。

## 版本来源

发布版本需要同步三个位置：

| 文件 | 作用 |
| --- | --- |
| `cairn/pyproject.toml` | Python 包版本。 |
| `cairn/src/cairn/__init__.py` | FastAPI/OpenAPI 等运行时版本。 |
| `cairn/uv.lock` | lockfile 中本地 package version。 |

当前源码中 `cairn/pyproject.toml` 和 `cairn/src/cairn/__init__.py` 应保持一致。

## 本地开发运行

开发调试优先使用：

```bash
docker compose up --build
```

或手动：

```bash
uv run --project cairn cairn serve
uv run --project cairn cairn dispatch --config dispatch.yaml
```

顶层 `start.sh` 是生产目录重启脚本，不是普通开发启动脚本。它硬编码进入生产 checkout，并会重建服务镜像、重建 worker 镜像、删除旧动态 worker 容器、重启 dispatcher。

## 本机生产部署

部署脚本：

```text
scripts/deploy.sh
```

默认生产目录由 `scripts/deploy.sh` 内的 `PROD_DIR` 指定。公开文档使用占位路径表示：

```text
/path/to/carin-production
```

执行：

```bash
./scripts/deploy.sh
```

可选参数：

```bash
./scripts/deploy.sh --skip-tests
./scripts/deploy.sh --no-backup
```

脚本执行顺序：

1. 校验开发目录和生产目录都是 Git 仓库。
2. 校验两个目录 `origin` 一致。
3. 校验两个目录都在 `main` 分支。
4. 拒绝部署有 tracked 改动的开发或生产目录。
5. 默认在 `cairn/` 运行 `uv run --group dev pytest -s`。
6. 推送开发目录 `main` 到 origin。
7. 在生产目录 `git pull --ff-only origin main`。
8. 默认备份生产 `datas/` 到 `datas.backup/<timestamp>/`。
9. 调用生产目录 `./start.sh` 重建和重启服务。
10. 访问 `http://127.0.0.1:8000/projects` 做健康检查。

保护项：

- 脚本不会自动 `git add`、`git commit` 或 `git reset --hard`。
- 脚本不会覆盖生产本地 `dispatch.yaml`、`datas/`、`.venv-test/`。
- 生产目录如有 tracked 改动，脚本会停止。

## Worker 镜像发布

GitHub Actions：

```text
.github/workflows/build-container-ghcr.yml
```

触发：

- 手动 workflow。
- `main` 分支上 `container/**` 变更。

产物：

```text
ghcr.io/<owner>/cairn-worker-container:latest
```

本地生产可选择使用 GHCR 镜像，也可使用 `start.sh` 本地构建 `cairn-worker-container:latest`。需要在 `dispatch.yaml` 的 `container.image` 中明确选择。

## Release 流程

推荐步骤：

1. 同步版本来源文件。
2. 运行全量测试。
3. 检查 README 和 docs 链接。
4. 检查敏感信息没有进入文档和示例。
5. 提交 release commit。
6. 创建 tag。
7. 推送 `main` 和 tag。

示例：

```bash
cd cairn
uv run --group dev pytest -s
```

```bash
git commit -m "chore: release v1.9"
git tag v1.9
git push origin main
git push origin v1.9
```

## 当前发布记录

### v1.9

- 新增 AI seeded vuln fork 工作流，recon snapshot 可创建 `fork_seed` ephemeral job，由 AI fork planner 生成 child vuln 初始 seed facts。
- Recon 改为功能理解优先，默认提示词优先建立页面/功能、用户动作、业务流程和 route/API 绑定。
- `facts` 表新增 `fact_type`、`title`、`summary`、`details_json`，保留旧 `description` 兼容旧项目和 YAML export。
- UI 增强 feature fact、judge checklist score/evidence 和 AI seeded fork seed 摘要展示。
- 修复 stopped recon 上 judge/fork_seed 的调度语义，并补充 fork seed、structured facts、prompt contract、DB migration、Server API 和 worker task 测试。
- 文档体系从旧中文文档目录迁移到 `docs/`，并改为使用 `docs/superpowers/specs/` 和 `docs/superpowers/plans/` 记录 Superpowers 驱动的设计与执行计划。

## 公开仓库前检查

- README 明确说明本仓库是 `oritera/Cairn` 的 modified version。
- README 不包含下游无权承诺的商业授权、PR 双授权或 personal-use 限制表述。
- `LICENSE` 保持标准 GNU AGPLv3 正文。
- 文档不包含真实 `dispatch.yaml`、Cookie、API key、账号、目标资产或运行证据。
- `docs/` 中不存在指向旧中文文档目录的链接。
- 测试命令统一，避免同时出现过期 venv 或本机临时路径作为默认路径。
