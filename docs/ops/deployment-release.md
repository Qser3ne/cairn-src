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

脚本执行顺序：

1. 校验开发目录和生产目录存在。
2. 校验本机可用 `rsync`。
3. 使用 `rsync -a --delete` 将开发目录直接覆盖到生产目录。

同步排除项：

- `.git/`
- `.github/`
- `.agents/`
- `.superpowers/`
- `.worktrees/`
- `.pytest_cache/`
- `datas.backup/`

脚本会覆盖生产 `datas/`，不会运行测试，不使用 Git 同步，不调用 `./start.sh` 重建/重启服务，也不执行 HTTP 健康检查。

保护项：

- 脚本不会自动 `git add`、`git commit`、`git push`、`git pull` 或 `git reset --hard`。
- 脚本不会覆盖生产本地 `.git/`、`.github/`、`.agents/`、`.superpowers/`、`.worktrees/`、`.pytest_cache/` 和 `datas.backup/`。

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

- 工作流收敛为 vuln-only collection/validation/report：collection 收集功能、API、认证边界和 validation seed；validation 写 findings；report 生成 SRC 报告草稿。
- Dispatcher worker 能力改为 `collection_reason`、`collection_explore`、`validation_reason`、`validation_explore` 和 `report`。
- `facts` 表新增 `fact_type`、`title`、`summary`、`details_json`，保留旧 `description` 兼容旧项目和 YAML export。
- Server 增加 task-mode write guards：collection 不能写 findings，report intent 必须通过 report endpoint 完成。
- 补充 DB/model task mode migration、Server guard、Dispatcher dual reason scheduling、Prompt contract 和 worker task 测试。
- 旧 recon snapshot、AI seeded fork、judge 和 fork_seed 只作为 legacy/migration 历史处理，不作为 active workflow。
- 文档体系从旧中文文档目录迁移到 `docs/`，并改为使用 `docs/superpowers/specs/` 和 `docs/superpowers/plans/` 记录 Superpowers 驱动的设计与执行计划。

## 公开仓库前检查

- README 明确说明本仓库是 `oritera/Cairn` 的 modified version。
- README 不包含下游无权承诺的商业授权、PR 双授权或 personal-use 限制表述。
- `LICENSE` 保持标准 GNU AGPLv3 正文。
- 文档不包含真实 `dispatch.yaml`、Cookie、API key、账号、目标资产或运行证据。
- `docs/` 中不存在指向旧中文文档目录的链接。
- 测试命令统一，避免同时出现过期 venv 或本机临时路径作为默认路径。
