cd /home/qser3ne/Application/carin

KALI_MIRROR=${KALI_MIRROR:-http://kali.download/kali}

# 1. 重建主服务镜像并重启 compose 服务
docker compose up --build -d

# 2. 重建 worker 镜像
docker build \
  --progress=plain \
  -t cairn-worker-container:latest \
  ./container

# 3. 删除旧的动态 worker 容器，让 dispatcher 后续按新镜像重建
docker rm -f $(docker ps -aq --filter 'name=^cairn-dispatch-') 2>/dev/null || true

# 4. 重启 dispatcher，触发后续调度使用新 worker 镜像
docker compose restart cairn-dispatcher
