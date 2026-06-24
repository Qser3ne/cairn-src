# Cairn SRC 文档

本目录是 Cairn SRC 的中文文档入口。旧中文文档目录的内容已经迁移到这里，并按读者和维护场景重新分组。

## 推荐阅读路径

| 读者 | 阅读顺序 |
| --- | --- |
| 初次运行者 | [`user/quickstart.md`](./user/quickstart.md) -> [`ops/configuration-security.md`](./ops/configuration-security.md) -> [`user/src-workflow.md`](./user/src-workflow.md) |
| 使用者 | [`user/src-workflow.md`](./user/src-workflow.md) -> [`architecture/worker-contracts.md`](./architecture/worker-contracts.md) |
| 开发者 | [`architecture/overview.md`](./architecture/overview.md) -> [`architecture/server-api.md`](./architecture/server-api.md) -> [`architecture/dispatcher.md`](./architecture/dispatcher.md) |
| 运维/发布维护者 | [`ops/worker-container.md`](./ops/worker-container.md) -> [`ops/deployment-release.md`](./ops/deployment-release.md) -> [`development/testing.md`](./development/testing.md) |

## 文档地图

| 文档 | 内容 |
| --- | --- |
| [`architecture/overview.md`](./architecture/overview.md) | 技术栈、整体架构、模块边界。 |
| [`architecture/server-api.md`](./architecture/server-api.md) | FastAPI Server、路由、API 语义。 |
| [`architecture/data-model.md`](./architecture/data-model.md) | Project、Fact、Intent、Finding、Snapshot、SQLite schema 与迁移。 |
| [`architecture/dispatcher.md`](./architecture/dispatcher.md) | Dispatcher 调度循环、并发、租约、容器生命周期。 |
| [`architecture/worker-contracts.md`](./architecture/worker-contracts.md) | reason、explore、judge、fork_seed、report 的 JSON 契约。 |
| [`architecture/prompts.md`](./architecture/prompts.md) | prompt group、默认 prompt 布局、占位符校验。 |
| [`user/quickstart.md`](./user/quickstart.md) | 从配置到启动的最短路径。 |
| [`user/src-workflow.md`](./user/src-workflow.md) | recon -> judge -> snapshot -> fork vuln -> report 工作流。 |
| [`ops/configuration-security.md`](./ops/configuration-security.md) | 配置文件、Cookie、SQLite、证据文件和 API key 的安全边界。 |
| [`ops/worker-container.md`](./ops/worker-container.md) | Worker 镜像、工具链、目录约定和 GHCR 构建。 |
| [`ops/deployment-release.md`](./ops/deployment-release.md) | 本机生产部署、版本同步、发布检查。 |
| [`development/testing.md`](./development/testing.md) | 测试命令、覆盖矩阵和质量门禁。 |

## Superpowers 目录

`docs/superpowers/` 保存本项目内的设计与执行计划记录：

- `docs/superpowers/specs/`：设计规格。
- `docs/superpowers/plans/`：可执行计划。

这些文档用于维护过程追踪，不替代面向用户的 `docs/user/`、`docs/architecture/` 和 `docs/ops/` 文档。

## 安全说明

文档只使用 `dispatch.example.yaml`、`dispatch_mock.yaml`、源码和测试作为公开信息来源。真实 `dispatch.yaml`、`datas/`、Cookie session、API key、运行证据、浏览器 profile 和 worker 输出都可能包含敏感信息，不应复制到文档或提交到仓库。
