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
- Worker 镜像通过 `container/Dockerfile` 从 `kalilinux/kali-rolling:latest` 本地构筑，而不是复用预构筑 worker 基镜像。
- Worker 镜像不再安装 `kali-linux-headless`；基础系统、黑盒 SRC 常用工具、少量白盒/依赖审计工具和 Playwright Chromium 运行库由 Dockerfile 显式包列表控制。
- Worker 镜像不再内置大型知识库和重型 POC 仓库；如任务需要，应通过运行时只读挂载提供源码、样本或外部资料。
- Worker 镜像固定创建 `/home/kali/workspace`、`/home/kali/reports`、`/home/kali/evidence`、`/home/kali/targets`、`/home/kali/cache`，并统一归属 `kali:kali`。
- Worker 镜像构筑支持 `OSV_SCANNER_VERSION` build arg，用于固定 `osv-scanner` 版本；默认入口在项目根 `start.sh` 中。
- `start.sh` 当前对 worker 镜像使用 `--pull --progress=plain` 重建，优先获取最新基镜像并输出完整构筑日志。
- Worker 构筑默认保留 Docker 层缓存；如需完全重建，可在手动构筑时额外追加 `--no-cache`。
- Dockerfile 中的 apt 层保留 BuildKit cache mount 和 apt 网络重试配置，用于降低中途失败后的重复下载成本。

## 验收方式

- 配置解析：旧配置不写 `container.init` 时应解析为 `true`；显式写 `false` 时应保留关闭状态。
- 单元测试：`ContainerManager` 创建项目容器和 startup healthcheck 容器时应把 `init` 参数传给 Docker SDK。
- 运行时验证：重建 worker 容器后执行 `docker inspect <worker> --format '{{.HostConfig.Init}}'`，预期输出 `true`。
- 进程验证：运行 Playwright 任务后检查 `ps -eo stat,cmd`，不应持续累积 `Z` 状态 Chrome/Playwright 子进程。

## 验证命令

- 项目目录有 `uv` 时运行：`uv run pytest cairn/tests/test_runtime_logic.py cairn/tests/test_config_and_adapters.py`
- 当前环境若缺少 `uv`，可在临时虚拟环境中安装本地包后从 `cairn/` 目录运行：`pytest -s tests/test_runtime_logic.py tests/test_config_and_adapters.py`
- Worker 镜像构筑可用以下命令验证 `OSV_SCANNER_VERSION` 是否进入构筑上下文：

  ```bash
  docker build \
    --build-arg OSV_SCANNER_VERSION="${OSV_SCANNER_VERSION:-v2.4.0}" \
    -f- /tmp <<'EOF'
  FROM kalilinux/kali-rolling:latest
  ARG OSV_SCANNER_VERSION
  RUN printf 'OSV_SCANNER_VERSION=%s\n' "$OSV_SCANNER_VERSION"
  EOF
  ```
