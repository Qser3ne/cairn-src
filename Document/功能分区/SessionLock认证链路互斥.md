# SessionLock认证链路互斥

## 目标

`session_lock` 用于避免多个 worker 同时操作同一项目的登录态、Cookie、Token、账号状态或共享认证上下文，导致认证链路互相干扰。

## 行为边界

- 项目级开关为 `session_lock_enabled`，新项目默认开启，项目设置中可关闭。
- reason 创建 intent 时必须显式输出 `session_lock`。
- 手动创建 intent 时也可选择 `session_lock`，默认跟随当前项目开关。
- 当项目开启 `session_lock_enabled` 时，同一项目同一时间只允许一个 `session_lock=true` 的 explore 任务运行。
- 当项目关闭 `session_lock_enabled` 时，dispatcher 恢复原有并发逻辑，只受 `max_project_workers` 等旧限制影响。

## 排队优先

- 如果某个 `session_lock=true` intent 因同项目已有 locked RunningTask 而无法派发，dispatcher 会把它加入本地等待队列。
- 当前 locked RunningTask 结束后，等待队列中的 locked intent 优先于普通 reason/explore 派发。
- 队列只保存在当前 dispatcher 进程内；项目 stopped、completed、deleted 或关闭 session lock 后会清理对应队列。

## 验收方式

- 新建项目默认显示 session lock 开启。
- 手动 intent 可以保存 `session_lock=true` 并在详情、导出中看到。
- 同项目已有 locked explore 运行时，新的 locked intent 不并发运行。
- locked explore 结束后，先执行之前因锁等待的 locked intent。
- 项目关闭 session lock 后，同项目 locked intents 可按旧逻辑并发调度。
