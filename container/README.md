```bash
BUILD_HTTP_PROXY=${BUILD_HTTP_PROXY:-http://http.docker.internal:3128}
BUILD_HTTPS_PROXY=${BUILD_HTTPS_PROXY:-$BUILD_HTTP_PROXY}
BUILD_NO_PROXY=${BUILD_NO_PROXY:-127.0.0.1,localhost,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16}

docker build \
  --build-arg http_proxy="${BUILD_HTTP_PROXY}" \
  --build-arg https_proxy="${BUILD_HTTPS_PROXY}" \
  --build-arg no_proxy="${BUILD_NO_PROXY}" \
  -t cairn-worker-container:latest \
  .
```

说明：

- 这是从 `kalilinux/kali-rolling:latest` 开始的全量本地构筑，不依赖上游预构筑 worker 镜像。
- `docker build` 不会自动继承 WSL 里 `127.0.0.1:7897` 这样的代理设置，必须显式传 `--build-arg`。
- 默认代理指向 Docker Desktop 可达的 `http.docker.internal:3128`；如果本机代理端口不同，用 `BUILD_HTTP_PROXY` / `BUILD_HTTPS_PROXY` 覆盖。
- 如果你确认本机 Clash/代理监听能长期稳定承载大流量下载，也可以手动覆盖成 `http://host.docker.internal:7897` 一类地址；但弱网下实测 `3128` 更稳。
- 首次构筑仍然很重，但 Dockerfile 已拆成多层。中途中断后再次执行同一命令，会复用已完成层，明显减少重下成本。
