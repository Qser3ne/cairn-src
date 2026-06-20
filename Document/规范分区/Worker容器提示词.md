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
- 已要求长输出、扫描日志、截图、PoC 输出和请求响应摘要保存到 `/home/kali/evidence`，最终报告草稿保存到 `/home/kali/reports`，并在结论中引用路径。
- 已约定 `/home/kali/workspace` 用于任务过程文件，`/home/kali/targets` 用于只读挂载源码、样本或安装包，`/home/kali/cache` 用于工具缓存。
- `container/AGENTS.md` 明确 worker 黑盒 SRC 工具优先，`semgrep`、`gitleaks`、`pip-audit`、`retire`、`osv-scanner` 只作为白盒和依赖审计辅助；白盒结果必须落到真实外部入口和可复现攻击链，不能单独作为 SRC finding。
- Web SRC 轻量工具链包括 `subfinder`、`dnsx`、`tlsx`、`interactsh-client`、`gau`、`waybackurls`、`uro`、`qsreplace`、`anew`、`kxss`、`gf`；这些工具多数用于产生线索，不能把扫描结果直接作为漏洞。
- `interactsh-client` 仅用于当前授权目标的 OOB 验证，结论中必须记录 interaction ID、触发时间、目标入口和请求摘要。
- 当前 `container/Dockerfile` 采用 `kalilinux/kali-rolling:latest` 作为基镜像。
- 已移除 `kali-linux-headless`，改为显式安装 SRC worker 常用包，避免 Kali headless 元包拉取上千个不常用依赖。
- 显式 apt 包覆盖基础 CLI、Python/Node、网络诊断、Web/SRC 扫描、目录枚举、AD/内网辅助、移动端基础分析、Playwright Chromium 运行库、云 CLI 前置依赖。
- `ripgrep` 和 `fd-find` 改从 Kali apt 源安装，并创建 `/usr/local/bin/fd -> /usr/bin/fdfind` 兼容链接，减少 GitHub release 下载点。
- Dockerfile 不再内置 `/home/kali/knowledges` 大型知识库，也不再创建 `/home/kali/pocs` 重型 POC 仓库目录。
- 构筑仍保留 apt 缓存 mount 和 apt 网络重试配置；`osv-scanner` 可通过 `--build-arg OSV_SCANNER_VERSION=<version>` 固定版本。

## 验收方式

- `container/AGENTS.md` 不应再包含 `未填写` 占位符。
- `container/AGENTS.md` 应包含严重漏洞、高危漏洞、中危漏洞、低危漏洞、忽略问题五类 SRC 评级参考。
- `container/Dockerfile` 应继续保留以下复制逻辑：
  - `COPY ./AGENTS.md /home/kali/workspace/AGENTS.md`
  - `COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md`
- `container/Dockerfile` 不应再拉取 `PayloadsAllTheThings`，也不应再创建 `/home/kali/pocs`。
- `container/Dockerfile` 应创建 `/home/kali/workspace`、`/home/kali/reports`、`/home/kali/evidence`、`/home/kali/targets`、`/home/kali/cache`，并归属 `kali:kali`。
- `container/Dockerfile` 应提供 `subfinder`、`dnsx`、`tlsx`、`interactsh-client`、`gau`、`waybackurls`、`uro`、`qsreplace`、`anew`、`kxss`、`gf`，并预置 `/home/kali/.gf` 常用 patterns。
- 全量构筑命令应支持 `OSV_SCANNER_VERSION` 构筑参数，用于固定 `osv-scanner` 版本。
- 这是提示词和文档变更，不需要运行 Python 单元测试。
