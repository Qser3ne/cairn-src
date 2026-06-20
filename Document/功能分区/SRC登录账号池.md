# SRC AuthMode 账号池

## 目标

`auth_mode="authenticated"` 项目使用项目级账号池隔离登录态探索。账号池能力在 SRC-only 重构后继续保留，同时适用于 recon 和 vuln 项目；不存在 Standard 例外。

## 用户场景

- 用户创建 anonymous recon/vuln 项目时不提供账号，系统只规划未登录攻击面。
- 用户创建 authenticated recon/vuln 项目时必须提供至少一个账号，系统只规划登录态攻击面。
- 多个账号可并发探索不同 explore intent；同一个账号同一时刻只租给一个 explore worker。
- 从 recon fork authenticated vuln 时，必须在 fork 请求中重新提供 child 项目的 accounts。

## 行为边界

- `auth_mode` 是项目级字段，不写入 intent。
- `accounts` 只允许在 `auth_mode="authenticated"` 时提交；anonymous 项目提交账号会返回 422。
- authenticated 项目无 accounts 会返回 422。
- 账号以明文保存在本地 SQLite 的 `project_accounts` 表，并会出现在项目详情和 YAML export 中，便于 worker prompt 直接使用。
- 账号字段为 `label`、`username`、`password`；缺省 label 由服务端生成为 `account-N`。
- Dispatcher 只对 authenticated 项目的 `explore` intent 做账号租约；`reason`、`judge`、`report` 不领取账号。
- 浏览器 profile、Cookie、Token、缓存和临时登录状态必须写入账号专属隔离目录。

## 验收方式

- authenticated recon/vuln 无账号创建返回 422。
- authenticated recon/vuln 有账号时创建成功，项目详情返回 `accounts`。
- anonymous 项目提交 accounts 返回 422。
- 导出 YAML 包含 `project.auth_mode` 和明文账号。
- 3 个账号、2 个 open explore intents 时能派发 2 个 worker；账号全忙时新 explore intent 入队，释放账号后队首立即补派。
