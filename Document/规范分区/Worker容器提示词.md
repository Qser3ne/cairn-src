# Worker容器提示词

## 模块边界

- `container/AGENTS.md` 是 Cairn worker 容器内代理的通用运行指令。
- `container/Dockerfile` 会把该文件复制为 `/home/kali/workspace/AGENTS.md` 和 `/home/kali/workspace/CLAUDE.md`，分别供 Codex/Claude 类 worker 读取。
- 任务级 JSON 输出契约仍由 `cairn/src/cairn/dispatcher/prompts/default/*/*.md` 控制，`container/AGENTS.md` 只提供环境、工具、安全边界、OOB 和授权凭据使用规则。

## 当前状态

- 已将 worker 环境说明重写为通用运行指令。
- 已明确公网 OOB/反连服务器 `43.159.131.239:22222` 与授权登录账号的使用场景。
- 已加入安全红线：不得爆破、不得修改目标文件或数据、不得影响目标业务可用性。
- 已加入涂鸦 SRC 漏洞评级与忽略范围，供 worker 判断 finding 严重性和是否应提交。
- 已要求长输出保存到 workspace 文件，并在结论中引用路径。
- 当前 `container/Dockerfile` 继续采用从 `kalilinux/kali-rolling:latest` 开始的全量本地构筑。
- 为减轻弱网重试成本，构筑已拆成多层，并对 `apt/git/npm/pip/wget/curl/playwright` 统一引入构筑期代理与重试策略。
- Docker build 不会自动继承 WSL 里的本地代理，例如 `127.0.0.1:7897`；必须通过 `--build-arg http_proxy/https_proxy/no_proxy` 显式传入。
- 默认构筑代理入口由 `start.sh` 提供：`BUILD_HTTP_PROXY`、`BUILD_HTTPS_PROXY`、`BUILD_NO_PROXY`。
- 当前环境下默认值改为 `http://http.docker.internal:3128`，因为实测它对 `apt/git/pip/wget` 的稳定性高于 `host.docker.internal:7897`。

## 验收方式

- `container/AGENTS.md` 不应再包含 `未填写` 占位符。
- `container/AGENTS.md` 应包含严重漏洞、高危漏洞、中危漏洞、低危漏洞、忽略问题五类 SRC 评级参考。
- `container/Dockerfile` 应继续保留以下复制逻辑：
  - `COPY ./AGENTS.md /home/kali/workspace/AGENTS.md`
  - `COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md`
- `container/Dockerfile` 不应再拉取 `PayloadsAllTheThings`，也不应再创建 `/home/kali/pocs`。
- 全量构筑命令应支持 `BUILD_HTTP_PROXY`、`BUILD_HTTPS_PROXY`、`BUILD_NO_PROXY` 三个可覆盖的构筑期环境变量。
- 这是提示词和文档变更，不需要运行 Python 单元测试。
