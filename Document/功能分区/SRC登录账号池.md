# SRC AuthMode Cookie Session 池

## 目标

recon 使用 `auth_mode="dual"` 和必填 cookie session 池同时支持未登录、已登录两条信息收集线路；vuln 继续使用 `auth_mode="anonymous|authenticated"` 决定漏洞挖掘范围。cookie session 池只服务于 `auth_scope="authenticated"` 的 explore intent。

## 用户场景

- 用户创建 recon 项目时必须提供至少一个 cookie session；reason 首轮创建 `anonymous` 与 `authenticated` 两条 baseline intent。
- 用户创建 anonymous vuln 项目时不提供 cookie session，系统只规划未登录漏洞挖掘范围。
- 用户创建 authenticated vuln 项目时必须提供至少一个 cookie session，系统只规划登录态漏洞挖掘范围。
- 多个 cookie session 可并发探索不同 explore intent；同一个 session 同一时刻只租给一个 explore worker。
- 从 recon fork authenticated vuln 时，必须在 fork 请求中重新提供 child 项目的 accounts。

## 行为边界

- `projects.auth_mode` 允许 `anonymous|authenticated|dual`；`dual` 只用于 recon。
- `intents.auth_scope` 允许 `anonymous|authenticated`；report intent 不使用 cookie session，可为 null。
- recon 项目必须提交 accounts，且不能显式选择 anonymous/authenticated auth mode。
- vuln anonymous 提交 accounts 返回 422；vuln authenticated 无 accounts 返回 422。
- cookie session 以明文 JSON 保存在本地 SQLite 的 `project_accounts.cookies_json`，并会出现在项目详情和 YAML export 中，便于 worker prompt 直接使用。
- 每组 session 字段为 `label` 和 `cookies`；`cookies` 是不限数量的 `{name, value}` 对，同组 cookie name 不允许重复；缺省 label 由服务端生成为 `account-N`。
- Dispatcher 只对 `auth_scope="authenticated"` 的 `explore` intent 做 session 租约；`auth_scope="anonymous"`、`reason`、`judge`、`report` 不领取 session。
- 浏览器 profile、Cookie、Token、缓存和临时登录状态必须写入 session 专属隔离目录。

## 验收方式

- recon 无 cookie session 创建返回 422，带 session 创建成功并返回 `auth_mode="dual"`。
- vuln authenticated 无 session 创建返回 422，有 session 时创建成功，项目详情返回 `accounts`。
- vuln anonymous 项目提交 accounts 返回 422。
- 导出 YAML 包含 `project.auth_mode` 和明文 cookie session。
- anonymous explore 不被 session 池阻塞；authenticated explore 在 session 全忙时入队，释放 session 后队首立即补派。
