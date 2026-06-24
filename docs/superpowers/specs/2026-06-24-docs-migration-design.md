# Docs Migration Design

## 目标

把旧中文文档目录迁移为标准 `docs/` 文档体系，并保留 `docs/superpowers/` 作为后续设计规格与执行计划目录。迁移完成后删除旧目录，避免双文档体系长期并存。

## 背景

项目当前是 Cairn SRC fork，核心工作流为 `recon -> judge -> snapshot -> fork vuln -> report`。旧文档集中在功能分区和规范分区，内容覆盖 Server 协议、Dispatcher 调度、Worker 容器、AuthMode 账号池、SRC 工作流、发布流程和 AI 续接上下文。

旧目录存在三个问题：

- 路径不符合常见仓库文档习惯，也不符合 Superpowers 对规格和计划文档的默认目录约定。
- 部分文档含有历史续接信息、重复信息和过期示例。
- README 与文档入口分散，不利于用户、开发者和运维按角色阅读。

## 目标结构

```text
docs/
  README.md
  architecture/
    overview.md
    server-api.md
    data-model.md
    dispatcher.md
    worker-contracts.md
    prompts.md
  user/
    quickstart.md
    src-workflow.md
  ops/
    configuration-security.md
    worker-container.md
    deployment-release.md
  development/
    testing.md
  superpowers/
    specs/
      2026-06-24-docs-migration-design.md
    plans/
      2026-06-24-docs-migration.md
```

## 信息来源

主要来源文件：

- `README.md`
- `cairn/pyproject.toml`
- `cairn/src/cairn/cli.py`
- `cairn/src/cairn/server/`
- `cairn/src/cairn/dispatcher/`
- `cairn/tests/`
- `dispatch.example.yaml`
- `dispatch_mock.yaml`
- `docker-compose.yaml`
- `Dockerfile`
- `container/README.md`
- `container/Dockerfile`
- `.github/workflows/build-container-ghcr.yml`
- `scripts/deploy.sh`
- 旧中文文档目录下全部 Markdown 文档

不作为公开内容来源：

- 真实 `dispatch.yaml`
- `datas/`
- `.agents/`
- worker 运行证据
- 真实 Cookie、账号、token、API key、目标资产和 OOB 资源信息

## 迁移策略

采用“合并重写后删除旧目录”的方式：

1. 先建立新 `docs/` 结构。
2. 将旧文档按读者角色重组，而不是逐文件平移。
3. 对过期或冲突内容以源码、测试和当前 README 为准。
4. 将敏感运行指令抽象为安全边界，不复制真实值。
5. 更新顶层 `README.md` 的文档入口和旧链接。
6. 删除旧中文文档目录。
7. 用 grep、测试和人工审查验证迁移结果。

## 决策

- `docs/user/` 面向使用者，保留快速开始和 SRC 工作流。
- `docs/architecture/` 面向开发者，解释 Server、Dispatcher、数据模型、Worker contract 和 prompt 体系。
- `docs/ops/` 面向运维和发布，覆盖配置安全、worker container、部署发布。
- `docs/development/` 面向维护者，记录测试和质量门禁。
- `docs/superpowers/` 只存放设计和执行计划记录，不替代正式用户文档。
- 删除旧中文文档目录，不保留兼容软链接。

## 过期内容处理

- Judge checklist 以当前 prompt/test 使用的 `feature_coverage` 和 `feature_api_mapping_quality` 为准，不沿用旧示例中的 `asset_coverage` 和 `endpoint_coverage`。
- 测试命令统一推荐 `cd cairn && uv run --group dev pytest -s`。
- `start.sh` 明确标注为生产目录重启脚本，不作为普通开发启动入口。
- Worker 容器提示词中的敏感运行资源不进入公开 docs，只保留安全原则。

## 验收标准

- 顶层 `README.md` 指向 `docs/`，不再链接旧中文文档目录。
- `docs/` 包含用户、架构、运维、开发和 Superpowers 文档。
- 旧中文文档目录不存在。
- `old_doc_path="Document$(printf /)" && rg "$old_doc_path" README.md docs cairn container scripts .github` 无旧链接残留。
- `cd cairn && uv run --group dev pytest -s` 通过。
- 文档中没有真实凭据、Cookie、账号、目标资产或运行证据。
