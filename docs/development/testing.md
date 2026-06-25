# 测试与质量门禁

本文记录本项目常用测试命令、覆盖范围和文档修改后的检查项。

## 推荐命令

全量测试：

```bash
cd cairn
uv run --group dev pytest -s
```

当前测试配置：

```text
cairn/pyproject.toml
```

测试目录：

```text
cairn/tests/
```

语法检查：

```bash
python3 -m compileall -q cairn/src/cairn cairn/tests
```

GitHub Actions：

- `.github/workflows/python-ci.yml` 在 push 和 pull request 时安装 `uv`，同步 dev 依赖，并运行 `uv run --group dev pytest -s`。

## 测试覆盖矩阵

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_server_api.py` | Project、recon/vuln、judgement、snapshot、fork、finding/report API。 |
| `test_db_migrations.py` | Legacy schema 迁移、standard 移除、structured facts 迁移。 |
| `test_scheduler_logic.py` | 调度规则、reason trigger、账号池、stopped/completed 行为。 |
| `test_worker_tasks.py` | reason/explore/judge/fork_seed/report task writeback。 |
| `test_contracts_and_drivers.py` | JSON 解析、contract 校验、worker driver 行为。 |
| `test_config_and_adapters.py` | dispatcher config、worker env、mock 行为、adapter 命令。 |
| `test_runtime_logic.py` | runtime、container、heartbeat、cancellation。 |
| `test_container_archives.py` | 容器归档/清理相关逻辑。 |
| `test_protocol_and_startup.py` | Server protocol client 与 startup healthcheck。 |
| `test_prompt_contracts.py` | 默认 prompt 占位符、中文软约束、任务契约。 |
| `test_recon_prompt_fixtures.py` | Recon prompt fixture 的 YAML 形态和脱敏规则。 |
| `test_mock_end_to_end.py` | Mock worker E2E 调度链路。 |

## 修改类型与验证建议

| 修改类型 | 最小验证 | 推荐验证 |
| --- | --- | --- |
| 仅 Markdown 文档 | 链接/敏感信息检查 | 全量测试确认无路径引用破坏。 |
| README 或 docs 链接 | `old_doc_path="Document$(printf /)" && rg "$old_doc_path" README.md docs` | 全量测试。 |
| Prompt 文本 | `uv run --group dev pytest -s tests/test_prompt_contracts.py tests/test_recon_prompt_fixtures.py` | 追加 worker task 和 contract 测试。 |
| Server API/model/db | 相关 server/db 测试 | 全量测试。 |
| Dispatcher scheduler/runtime | 相关 scheduler/runtime 测试 | 全量测试。 |
| Worker adapter/config | config/adapters、contracts 测试 | 全量测试。 |
| CI workflow | workflow 语法与依赖安装路径检查 | 本地全量测试。 |
| Container Dockerfile | smoke test | CI/GHCR 构建。 |

## 文档迁移检查

删除旧中文文档目录后运行：

```bash
old_doc_path="Document$(printf /)"
rg "$old_doc_path" README.md docs cairn container scripts .github
```

期望：没有旧文档链接残留。

检查敏感字段：

```bash
rg "API_KEY|TOKEN|PASSWORD|COOKIE|dispatch.yaml|datas/|/home/kali/evidence|/home/kali/reports" README.md docs container/README.md
```

该命令会匹配安全说明中的字段名；人工确认没有真实值。

## Release 前质量门禁

发布前至少完成：

1. `cd cairn && uv run --group dev pytest -s` 通过。
2. README 和 `docs/` 链接无旧中文文档目录路径。
3. 文档不包含真实凭据、Cookie、目标资产或运行证据。
4. `cairn/pyproject.toml`、`cairn/src/cairn/__init__.py`、`cairn/uv.lock` 版本一致。
5. 若改动 `container/**`，确认本地构建或 GHCR workflow 路径可用。
