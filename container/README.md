```bash
KALI_MIRROR=${KALI_MIRROR:-http://kali.download/kali}

docker build \
  --build-arg KALI_MIRROR="${KALI_MIRROR}" \
  -t cairn-worker-container:latest \
  ./container
```

说明：

- 这是从 `kalilinux/kali-last-release:latest` 开始的本地构筑，不依赖上游预构筑 worker 镜像。
- Dockerfile 不再安装 `kali-linux-headless`，改为显式安装 SRC worker 常用包列表，避免 Kali headless 元包拉取大量不常用工具。
- 默认 apt 镜像源为 `http://kali.download/kali`；如果下载不稳定，用 `KALI_MIRROR` 覆盖为更近的 Kali 镜像。
- 首次构筑仍然需要下载安全工具、Playwright 浏览器和知识库；中途中断后再次执行同一命令，会复用已完成层。
