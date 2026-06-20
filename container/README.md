# Cairn SRC Worker Container

本目录用于构筑 Cairn 项目 worker 镜像。该镜像面向 Codex 驱动的授权 SRC 漏洞挖掘：黑盒工具优先，少量白盒和依赖审计工具用于辅助确认线索。

## 构建

```bash
docker build -t cairn-worker-container:latest ./container
```

如需固定 `osv-scanner` 版本：

```bash
docker build \
  --build-arg OSV_SCANNER_VERSION=v2.4.0 \
  -t cairn-worker-container:latest \
  ./container
```

## 镜像内容

- 基础镜像：`kalilinux/kali-rolling:latest`，固定 `linux/amd64`。
- 不安装 Kali headless 元包，改用显式 apt 包列表控制工具范围和镜像体积。
- 黑盒 SRC 工具包括 `nuclei`、`katana`、`dalfox`、`ffuf`、`feroxbuster`、`gobuster`、`dirsearch`、`nikto`、`sqlmap`、`naabu`、`whatweb`、`wafw00f`、`netexec`、`impacket-*` 等。
- Web SRC 轻量工具链包括 `subfinder`、`dnsx`、`tlsx`、`interactsh-client`、`gau`、`waybackurls`、`uro`、`qsreplace`、`anew`、`kxss`、`gf`，覆盖资产发现、URL 收集、参数整理和 XSS/OOB 辅助。
- 白盒和依赖审计工具包括 `semgrep`、`gitleaks`、`pip-audit`、`retire`、`osv-scanner`。
- 保留轻量手工工具和辅助材料：`jwt_tool`、`ysoserial.jar`、`jdwp-shellifier`、`cloudfox`、`kerbrute`、Playwright Chromium。
- `gf` 预置少量常用 Web SRC patterns，位于 `/home/kali/.gf`。
- 不再内置大型知识库和 POC 仓库；如任务需要，建议运行时以只读卷挂载。

## 目录约定

- `/home/kali/workspace`：任务过程文件、临时脚本和简短命令记录。
- `/home/kali/reports`：最终报告、复现步骤草稿和可交付摘要。
- `/home/kali/evidence`：扫描日志、请求响应摘要、截图、PoC 输出。
- `/home/kali/targets`：只读挂载的源码、样本、安装包或其他审计材料。
- `/home/kali/cache`：浏览器、包管理器或工具缓存。

推荐运行时挂载：

```bash
docker run --rm -it \
  -v "$PWD/workspace:/home/kali/workspace" \
  -v "$PWD/reports:/home/kali/reports" \
  -v "$PWD/evidence:/home/kali/evidence" \
  -v "$PWD/targets:/home/kali/targets:ro" \
  cairn-worker-container:latest
```

## Smoke Test

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
