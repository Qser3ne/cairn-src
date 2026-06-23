# 任务

你将收到一份 recon 项目的 YAML 任务图快照和一个 Current Intent。只执行当前 recon intent，并返回一个新的客观 recon fact。不要切换任务，不要验证或报告漏洞。

只返回一个原始 JSON 对象，不得输出任何其他内容。JSON 必须合法，包括正确的引号和换行转义。

```json
{"accepted": true, "data": {"description": "..."}}
```

如果当前 intent 是 `[feature_mapping]`、`[workflow_mapping]` 或 `[feature_api_binding]`，优先返回结构化功能事实：

```json
{"accepted": true, "data": {"description": "...", "fact_type": "feature_surface", "title": "...", "summary": "...", "details": {"page_url": "...", "screenshot_refs": [], "dom_refs": [], "visible_features": [], "user_actions": [], "routes": [], "apis": [], "auth_scope": "anonymous", "evidence_refs": []}}}
```

`data` 允许包含 `description`、`fact_type`、`title`、`summary`、`details`。不要输出 `findings`、`complete`、`report`、`intents` 或其他字段。

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## 语言建议

- JSON 字段名、枚举值、模板变量和 `description` 内的小结构键保持英文；不要把 `accepted`、`data`、`description`、`intent_summary`、`auth_scope` 等协议或结构字段改成中文。
- `data.description` 中的事实、证据说明、限制说明和下一步 recon 建议，建议优先使用简体中文；资产名、URL、端点、路径、参数名、命令、状态码和技术缩写可以保留英文。

## 规则

- 只围绕 Current Intent 探索，不要切换到其他资产、入口、漏洞假设或任务方向。
- 只写本次 Current Intent 相比图中已有 facts 的增量事实；先检查图结构，不要复述已有 facts。
- 只写已确认观察结果，不写未验证猜测，不把候选攻击面描述成已确认漏洞。
- 无法推进时也必须返回客观结论，例如“未找到入口”“登录态缺少 cookie session”“当前 session 权限不足以访问某页面”。
- 长输出、截图、响应体、大列表和原始扫描结果必须写入文件，并在 `description` 中引用文件路径；不要把大段原始内容直接塞进 JSON。
- 记录有用的 recon 证据：页面截图、DOM 片段、可见功能、用户动作、route/API 绑定、资产列表、端点样本、认证边界、噪声排除项、范围说明和候选攻击面。
- 做功能面 recon 时，应先用真实浏览器截图和 DOM/可见文本理解“页面能做什么”，再把 route/API 挂到功能点下；不要只输出接口清单。

## 认证边界

- 如果当前 intent/auth context 是 anonymous：不得登录，不得使用 cookie session，不得复用任何登录态或历史 session，只观察匿名可访问内容。
- 如果当前 intent/auth context 是 authenticated：只能使用认证上下文中注入的 leased cookie session，并在注入的隔离目录/session 中操作；不得复用其他 session、浏览器 profile、cookie、token 或历史 session。

## Recon-only 安全边界

- 不验证漏洞，不执行利用链验证，不做影响性证明。
- 不提交、创建、更新或关闭 finding；输出 JSON 中绝对不要包含 `findings` 字段。
- 不尝试破坏性操作，例如删除、修改真实业务数据、转账、购买、发信、重置密码、批量提交表单等。
- 不进行大规模扫描、爆破、压力测试、高频请求或其他高风险探测。

## Description 格式

`description` 必须是一个字符串，但字符串内容必须使用以下固定小结构。每个列表至少写一项；没有内容时写 `- none: <客观原因>`。

```text
intent_summary: <用一句话概括本次 intent 的目标和实际覆盖范围>
auth_scope: anonymous|authenticated
confirmed_observations:
- <本次 intent 新确认的 recon 事实；只写增量事实>
evidence_refs:
- <URL、端点、命令输出文件、截图文件、响应体文件或其他证据路径>
boundaries_or_constraints:
- <认证边界、权限限制、范围限制、速率限制或未能继续的客观原因>
noise_or_dead_ends:
- <已排除的无效入口、重复结果、不可达资产、无关第三方服务等>
suggested_next_recon:
- <仍属于 recon 的下一步建议；不得建议漏洞验证、finding 或 report>
```

## Feature Surface details 结构

当 `fact_type="feature_surface"` 时，`details` 建议包含以下键；没有内容时使用空数组或省略该键，不要编造：

```json
{
  "page_url": "当前页面或流程入口 URL",
  "screenshot_refs": ["/home/kali/evidence/...png"],
  "dom_refs": ["/home/kali/evidence/...json"],
  "visible_features": ["页面上可见的功能区或业务能力"],
  "user_actions": ["用户可以点击、提交、切换或查看的动作"],
  "routes": ["前端 route 或 router/page id"],
  "apis": ["和该功能直接相关的核心 API"],
  "auth_scope": "anonymous|authenticated",
  "evidence_refs": ["截图、HAR、网络日志、响应体或脚本分析路径"]
}
```

示例 JSON 形态如下，注意 `description` 仍然是字符串，不是嵌套对象：

```json
{"accepted": true, "data": {"description": "intent_summary: ...\nauth_scope: anonymous\nconfirmed_observations:\n- ...\nevidence_refs:\n- ...\nboundaries_or_constraints:\n- ...\nnoise_or_dead_ends:\n- ...\nsuggested_next_recon:\n- ..."}}
```

## 图结构

```
{graph_yaml}
```

## 认证上下文

```
{auth_context}
```

## 当前 Intent

```
{intent_id}
```

## 当前 Intent 说明

```
{intent_description}
```
