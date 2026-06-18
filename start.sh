cd /home/qser3ne/Application/carin

BUILD_HTTP_PROXY=${BUILD_HTTP_PROXY:-http://http.docker.internal:3128}
BUILD_HTTPS_PROXY=${BUILD_HTTPS_PROXY:-$BUILD_HTTP_PROXY}
BUILD_NO_PROXY=${BUILD_NO_PROXY:-127.0.0.1,localhost,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16}

# 1. 重建主服务镜像并重启 compose 服务
docker compose up --build -d

# 2. 重建 worker 镜像
docker build \
  --build-arg http_proxy="${BUILD_HTTP_PROXY}" \
  --build-arg https_proxy="${BUILD_HTTPS_PROXY}" \
  --build-arg no_proxy="${BUILD_NO_PROXY}" \
  -t cairn-worker-container:latest \
  ./container

# 3. 删除旧的动态 worker 容器，让 dispatcher 后续按新镜像重建
docker rm -f $(docker ps -aq --filter 'name=^cairn-dispatch-') 2>/dev/null || true

# 4. 重启 dispatcher，触发后续调度使用新 worker 镜像
docker compose restart cairn-dispatcher
