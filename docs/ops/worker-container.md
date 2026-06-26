# Worker 容器与镜像

`container/` 用于构建 Cairn 项目 worker 镜像。Dispatcher 不在 `docker-compose.yaml` 中直接声明项目 worker；它按项目动态创建 worker 容器。

## 构建

本地构建：

```bash
docker build -t cairn-worker-container:latest ./container
```

固定 `osv-scanner` 版本：

```bash
docker build \
  --build-arg OSV_SCANNER_VERSION=v2.4.0 \
  -t cairn-worker-container:latest \
  ./container
```

## 镜像内容

当前 worker 镜像基于 Kali rolling，固定 `linux/amd64`。镜像不安装大型 Kali headless 元包，而是显式安装 SRC worker 常用工具。

主要工具类别：

- 基础 CLI、Python/Node、网络诊断工具。
- 黑盒 SRC 工具，例如 `nuclei`、`katana`、`dalfox`、`ffuf`、`feroxbuster`、`gobuster`、`dirsearch`、`nikto`、`sqlmap`、`naabu`、`whatweb`、`wafw00f`。
- Web SRC 轻量工具链，例如 `subfinder`、`dnsx`、`tlsx`、`interactsh-client`、`gau`、`waybackurls`、`uro`、`qsreplace`、`anew`、`kxss`、`gf`。
- 白盒和依赖审计辅助工具，例如 `semgrep`、`gitleaks`、`pip-audit`、`retire`、`osv-scanner`。
- Playwright Chromium 运行环境。

工具输出只是线索，不能把扫描结果直接当成漏洞。白盒和依赖审计结果需要落到真实外部入口和可复现攻击链。

## 目录约定

Worker 容器内固定目录：

| 目录 | 用途 |
| --- | --- |
| `/home/kali/workspace` | 任务过程文件、临时脚本、简短命令记录。 |
| `/home/kali/reports` | 最终报告、复现步骤草稿、可交付摘要。 |
| `/home/kali/evidence` | 扫描日志、请求响应摘要、截图、PoC 输出。 |
| `/home/kali/targets` | 只读源码、样本、安装包或审计材料。 |
| `/home/kali/cache` | 浏览器、包管理器或工具缓存。 |

镜像构建会创建 `/home/kali/workspace/.agents` 和 `/home/kali/workspace/.claude` 空目录，并把 `container/AGENTS.md` 复制为 worker 指令文件。不要依赖本地未跟踪的 `.agents/` 目录参与镜像构建。

推荐挂载示例：

```bash
docker run --rm -it \
  -v "$PWD/workspace:/home/kali/workspace" \
  -v "$PWD/reports:/home/kali/reports" \
  -v "$PWD/evidence:/home/kali/evidence" \
  -v "$PWD/targets:/home/kali/targets:ro" \
  cairn-worker-container:latest
```

挂载整个 `/home/kali/workspace` 会遮蔽镜像内的 `AGENTS.md`、`CLAUDE.md`、`.agents/` 和 `.claude/`。本地交互调试如需保留 worker 指令，应把这些文件复制到挂载目录，或只挂载子目录。

## Dispatcher 动态容器

Dispatcher 创建项目 worker 容器时使用 `dispatch.yaml` 的 `container` 配置：

```yaml
container:
  image: ghcr.io/oritera/cairn-worker-container:latest
  network_mode: host
  init: true
  completed_action: stop
```

约定：

- 每个项目一个 `cairn-dispatch-<project_id>` 容器。
- 启动健康检查使用临时容器。
- Worker task 在项目容器内通过 Docker exec 执行。
- `init=true` 等价于 Docker `--init`，用于回收浏览器等子进程。
- `completed_action="stop"` 保留容器供排查；`remove` 更干净但会丢失容器内状态。

## GHCR 构建

GitHub Actions 文件：

```text
.github/workflows/build-container-ghcr.yml
```

触发方式：

- 手动 `workflow_dispatch`。
- `main` 分支上 `container/**` 变更。

产物标签：

```text
ghcr.io/${{ github.repository_owner }}/cairn-worker-container:latest
```

本地默认示例仍可能引用 upstream 镜像名。实际部署时应根据自己的发布策略在 `dispatch.yaml` 中明确选择镜像。

## Smoke Test

构建后可运行：

```bash
docker run --rm cairn-worker-container:latest bash -lc '
  codex --version &&
  nuclei -version &&
  semgrep --version &&
  pip-audit --version &&
  retire --version &&
  osv-scanner --version &&
  for c in subfinder dnsx tlsx interactsh-client gau waybackurls uro qsreplace anew kxss gf; do
    command -v "$c" >/dev/null || exit 1
  done &&
  gf -list | grep -E "xss|sqli|ssrf" &&
  test -d /home/kali/reports &&
  test -d /home/kali/evidence &&
  test -d /home/kali/targets &&
  test -d /home/kali/cache
'
```

## 安全边界

Worker 容器运行指令位于 `container/AGENTS.md`。该文件允许提交，但只能包含占位符和通用边界，不能写入真实 OOB 服务器、SSH 凭据、授权账号、Cookie 或目标资产。公开文档只总结通用边界：

- 只对授权目标运行。
- 最小化影响。
- 禁止爆破、撞库、密码喷洒、高频扫描、拒绝服务、资源耗尽和业务压测。
- 禁止修改目标业务数据或写入持久化后门。
- 发现敏感数据时只记录最小必要证据。
- 长日志、截图、PoC 输出保存到 evidence/report 目录，结论只引用路径和摘要。
