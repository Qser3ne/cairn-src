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
- 当前 `container/Dockerfile` 采用 `kalilinux/kali-last-release:latest` 作为基镜像，并通过 `KALI_MIRROR` 指向 `kali-last-snapshot` 源。
- 已移除 `kali-linux-headless`，改为显式安装 SRC worker 常用包，避免 Kali headless 元包拉取上千个不常用依赖。
- 显式 apt 包覆盖基础 CLI、Python/Node、网络诊断、Web/SRC 扫描、目录枚举、AD/内网辅助、Playwright Chromium 运行库、云 CLI 前置依赖。
- `ripgrep` 和 `fd-find` 改从 Kali apt 源安装，并创建 `/usr/local/bin/fd -> /usr/bin/fdfind` 兼容链接，减少 GitHub release 下载点。
- 构筑仍保留 apt 缓存 mount 和 apt 网络重试配置；如默认镜像源不可用，可通过 `--build-arg KALI_MIRROR=<mirror>` 覆盖。

## 验收方式

- `container/AGENTS.md` 不应再包含 `未填写` 占位符。
- `container/AGENTS.md` 应包含严重漏洞、高危漏洞、中危漏洞、低危漏洞、忽略问题五类 SRC 评级参考。
- `container/Dockerfile` 应继续保留以下复制逻辑：
  - `COPY ./AGENTS.md /home/kali/workspace/AGENTS.md`
  - `COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md`
- `container/Dockerfile` 不应再拉取 `PayloadsAllTheThings`，也不应再创建 `/home/kali/pocs`。
- 全量构筑命令应支持 `KALI_MIRROR` 构筑参数，用于切换 Kali apt 镜像源。
- 这是提示词和文档变更，不需要运行 Python 单元测试。
