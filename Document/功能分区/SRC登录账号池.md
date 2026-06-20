# SRC登录账号池

## 目标

`authenticated` SRC 项目用账号池隔离登录态探索。每个 explore worker 启动前必须领取一个项目账号，任务结束后释放账号；没有空闲账号时，新的 authenticated intent 进入 FIFO 等待队列。

## 用户场景

- 用户创建 SRC 项目时选择 `auth_mode=anonymous`，系统只规划未登录攻击面。
- 用户创建 SRC 项目时选择 `auth_mode=authenticated`，必须录入至少一个账号，系统只规划登录态攻击面。
- 多个账号可并发探索不同 intent；同一个账号同一时刻只分配给一个 worker。

## 行为边界

- `auth_mode` 是项目级设置，不写入 intent。
- `authenticated` 只支持 SRC 项目；standard 项目保持 `anonymous`。
- 账号以明文保存到本地 SQLite，并可在项目详情与导出中看到，便于 worker prompt 直接使用。
- 账号字段为 `label`、`username`、`password`，缺省 label 由服务端生成为 `account-N`。
- 浏览器 profile、Cookie、Token、缓存和临时登录状态必须写入账号专属隔离目录。

## 验收方式

- authenticated SRC 项目无账号创建返回 422。
- authenticated SRC 项目有账号时创建成功，项目详情返回 `accounts`。
- 导出 YAML 包含 `project.auth_mode` 和明文账号。
- 3 个账号、2 个 open intents 时能派发 2 个 worker；账号全忙时新 intent 入队，释放账号后队首立即补派。
