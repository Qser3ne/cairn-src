# Worker容器生命周期

## 模块边界

- Dispatcher 通过 `cairn/src/cairn/dispatcher/runtime/containers.py` 动态创建每个项目的 worker 容器。
- `docker-compose.yaml` 只启动 `cairn-server` 和 `cairn-dispatcher`，不直接声明项目 worker 容器。
- `dispatch.example.yaml` 和本地 `dispatch.yaml` 的 `container` 段控制动态 worker 容器的镜像、网络、完成后动作和 Linux capability。

## 当前约定

- `ContainerConfig.init` 默认值为 `true`，等价于 Docker CLI 的 `docker run --init`。
- `ContainerManager.ensure_running()` 创建项目 worker 容器时传入 Docker SDK `init=self._config.init`。
- `ContainerManager.create_startup_container()` 创建启动健康检查容器时也传入同一配置。
- 默认启用 init reaper 是为了让 worker 容器内 PID 1 回收 Playwright/Chrome 等子进程，避免长期运行时累积 zombie 进程。
- 如遇到不支持 Docker init 的特殊环境，可以在调度配置中设置 `container.init: false` 临时关闭。
- Worker 镜像仍通过 `container/Dockerfile` 从 `kalilinux/kali-rolling:latest` 全量构筑，而不是复用预构筑 worker 基镜像。
- Worker 镜像构筑依赖显式传入的 build args 代理；默认入口在项目根 `start.sh` 中：
  - `BUILD_HTTP_PROXY`
  - `BUILD_HTTPS_PROXY`
  - `BUILD_NO_PROXY`
- Docker build 不会自动继承 WSL 内的 `http_proxy=http://127.0.0.1:7897` 这类本地代理设置；必须转为 `host.docker.internal` 并通过 `--build-arg` 传入。
- 当前环境的默认构筑代理是 Docker Desktop 可达的 `http://http.docker.internal:3128`；如需改用本机代理监听端口，再覆盖 `BUILD_HTTP_PROXY` / `BUILD_HTTPS_PROXY`。
- 为了让大镜像失败后可续跑，worker 构筑不再使用 `--no-cache`，Dockerfile 中的外网安装步骤也已拆成多层。

## 验收方式

- 配置解析：旧配置不写 `container.init` 时应解析为 `true`；显式写 `false` 时应保留关闭状态。
- 单元测试：`ContainerManager` 创建项目容器和 startup healthcheck 容器时应把 `init` 参数传给 Docker SDK。
- 运行时验证：重建 worker 容器后执行 `docker inspect <worker> --format '{{.HostConfig.Init}}'`，预期输出 `true`。
- 进程验证：运行 Playwright 任务后检查 `ps -eo stat,cmd`，不应持续累积 `Z` 状态 Chrome/Playwright 子进程。

## 验证命令

- 项目目录有 `uv` 时运行：`uv run pytest cairn/tests/test_runtime_logic.py cairn/tests/test_config_and_adapters.py`
- 当前环境若缺少 `uv`，可在临时虚拟环境中安装本地包后从 `cairn/` 目录运行：`pytest -s tests/test_runtime_logic.py tests/test_config_and_adapters.py`
- Worker 镜像构筑可用以下命令验证代理是否进入构筑上下文：

  ```bash
  docker build \
    --build-arg http_proxy="${BUILD_HTTP_PROXY}" \
    --build-arg https_proxy="${BUILD_HTTPS_PROXY}" \
    --build-arg no_proxy="${BUILD_NO_PROXY}" \
    -f- /tmp <<'EOF'
  FROM kalilinux/kali-rolling:latest
  ARG http_proxy
  RUN printf 'http_proxy=%s\n' "$http_proxy"
  EOF
  ```
