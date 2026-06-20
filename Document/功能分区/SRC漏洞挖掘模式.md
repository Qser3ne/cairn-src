# SRC漏洞挖掘模式

## 目标

SRC 模式用于持续挖掘多个漏洞结果，而不是发现一个结果后自动完成项目。用户在新建项目时选择 `SRC Vulnerability Mining`，项目会进入 `mode=src`。

## 行为边界

- SRC 项目默认关闭 bootstrap，初始直接进入 reason 规划。
- 新建项目默认开启 session lock；SRC reason 对涉及登录态、Cookie、Token、账号状态或认证链路的 intent 输出 `session_lock=true`，避免多个 worker 并发干扰同一会话。
- Dispatcher 在 SRC reason 中不会调用项目 complete；即使模型返回 complete payload 也会忽略。
- 重复控制主要依赖 SRC reason prompt：创建新 intent 前必须分析已有 facts、open intents、concluded intents 和 findings，避免重复方向。
- 服务端只做明显重复兜底：同一项目内完全相同 `from` 集合和规范化后完全相同 `description` 的 intent 返回 409。
- SRC 挖掘以黑盒外部入口验证为主。少数白盒源码审计、依赖审计或密钥扫描结果只能作为线索，必须结合真实部署场景、外部可达入口和可复现攻击链后才能形成 finding。

## 输入输出

- 输入：项目 title、origin、goal、hints、mode。
- 输出：普通 facts/intents 图结构，以及可选 findings 列表。
- finding 字段包括标题、漏洞类型、严重性、目标、位置、影响、证据、复现、修复建议和状态。

## 验收方式

- 新建 SRC 项目时 `mode=src` 且 `bootstrap_enabled=false`。
- SRC 项目初始调度 reason，不调度 bootstrap。
- SRC reason 不会自动 complete。
- 项目详情和导出能看到 findings。
- 仅由静态代码审计或依赖扫描得到、无法证明真实入口和实际影响的问题，不应作为有效 SRC finding。
