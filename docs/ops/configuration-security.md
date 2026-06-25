# 配置安全指南

Cairn SRC 会处理目标范围、Cookie session、worker API key、扫描证据和 SQLite 数据库。配置和运行产物默认都应视为敏感数据。

## 可以提交的配置来源

| 文件 | 用途 | 是否可提交 |
| --- | --- | --- |
| `dispatch.example.yaml` | 真实 worker 配置示例，只保留占位值。 | 可以 |
| `dispatch_mock.yaml` | 本地 mock worker 示例。 | 可以 |
| `docker-compose.yaml` | Server 和 Dispatcher 的 compose 拓扑。 | 可以 |
| `Dockerfile`、`container/Dockerfile` | 主应用和 worker 镜像构建定义。 | 可以 |

## 不应提交的内容

`.gitignore` 已忽略：

- `dispatch.yaml`
- `datas/`
- `datas.backup/`
- `.venv-test/`
- `.agents/`

以下内容也不应粘贴到文档、issue、commit message 或测试 fixture：

- 真实 API key、token、模型服务凭据。
- Cookie session、账号凭据、浏览器 profile。
- SQLite 数据库和 YAML export 中的真实项目数据。
- worker 证据目录、请求响应摘要、截图、PoC 输出。
- 真实目标域名、内部资产、个人信息或业务敏感数据。
- 公网回连/OOB 资源的地址、账号或密码。

## `dispatch.yaml` 风险点

`dispatch.yaml` 是本地运行配置，通常包含：

- worker 后端模型名和 endpoint。
- API key 或 token。
- `common_env` 注入到所有 worker 的环境变量。
- 容器镜像和网络模式。
- 并发、超时、healthcheck 策略。

文档只应说明字段名和作用，不应复制真实值。公开示例统一基于 `dispatch.example.yaml` 或 `dispatch_mock.yaml`。

## Cookie Session 风险点

项目账号池以 Cookie pair 的形式保存：

```json
{"name": "sessionid", "value": "example-placeholder"}
```

实际运行中：

- Cookie 明文保存在本地 SQLite 的 `project_accounts.cookies_json`。
- Cookie 会出现在项目详情和 YAML export 中。
- Dispatcher 会把 authenticated explore 所需 Cookie 注入 worker prompt。
- Worker 需要把浏览器/session 状态写入 session 专属隔离目录。

因此，`datas/`、export 文件和 worker 工作目录都属于敏感运行产物。

## Worker 证据与报告

Worker 容器中建议的目录语义：

- `/home/kali/workspace`：任务过程文件、临时脚本、简短命令记录。
- `/home/kali/reports`：最终报告、复现步骤草稿、可交付摘要。
- `/home/kali/evidence`：扫描日志、请求响应摘要、截图、PoC 输出。
- `/home/kali/targets`：只读源码、样本、安装包或审计材料。
- `/home/kali/cache`：浏览器、包管理器或工具缓存。

公开文档只描述目录用途，不复制真实证据内容。

## Fixture 与测试数据

测试 fixture 必须使用保留域名和占位值：

- 使用 `.example.test`、`example.com`、`target.example` 等示例域名。
- 使用 `api-key-placeholder`、`token-placeholder` 等占位值。
- 不包含真实 Cookie、手机号、邮箱、内网地址、业务路径或漏洞证据。

`cairn/tests/test_collection_prompt_fixtures.py` 会检查 collection prompt fixture 是否包含 secret-looking 文本，并要求 fixture 域名使用 `.example.test`。

## 公开前检查

运行以下命令检查旧文档链接和敏感路径是否残留：

```bash
old_doc_path="Document$(printf /)"
rg "$old_doc_path" README.md docs cairn/tests
rg '/home/kali|dispatch.yaml|API_KEY|TOKEN|PASSWORD|COOKIE' README.md docs cairn/tests
```

命令可能匹配安全说明中的字段名；需要人工确认没有真实值。
