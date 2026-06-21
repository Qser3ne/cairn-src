# 任务

你现在处于 recon 项目的 conclude fallback 阶段。此前 execute 阶段可能已经超时、输出了非 JSON，或提前结束。你的任务不是继续完成探索，而是把当前上下文中已经确认的 Current Intent 增量 recon 结果整理成一个合法 JSON fact。

只返回一个原始 JSON 对象，必须满足以下 contract：

```json
{"accepted": true, "data": {"description": "..."}}
```

`data.description` 必须是非空字符串。不要添加 `findings`，不要输出 `complete`，不要输出 JSON 之外的解释文字。

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## fallback 阶段边界

你必须停止探索，只能基于当前上下文总结已确认信息：

- 不再运行新命令。
- 不访问新 URL。
- 不尝试登录。
- 不做额外验证。
- 不继续推进任务目标。

## description 内容结构

`data.description` 中必须包含以下结构，并只记录已确认的增量信息：

```text
fallback_reason: execute_timeout|parse_failure|early_exit|unknown
confirmed_incremental_facts:
- ...
evidence_refs:
- ...
unconfirmed_or_discarded:
- ...
next_safe_recon:
- ...
```

字段要求：

- `fallback_reason`：根据上下文尽量填写 `execute_timeout`、`parse_failure`、`early_exit` 或 `unknown`。当前代码无法直接注入 fallback reason；如果无法判断，写 `unknown`。
- `confirmed_incremental_facts`：只写本轮相对既有图结构新增且已确认的 recon 事实，例如已确认资产、端点、页面行为、响应特征、认证状态或可复现观察结果。
- `evidence_refs`：写支持上述事实的上下文证据引用，例如命令输出摘要、访问过的 URL、响应片段、状态码、日志线索或图结构中的相关节点。没有证据引用时写 `- none`。
- `unconfirmed_or_discarded`：写已出现但不能当作事实的假设、失败尝试、解析不完整内容或缺证据信息。没有时写 `- none`。
- `next_safe_recon`：写后续可安全继续的 recon 建议，但只能基于已确认事实提出，不要伪装成已经完成的探索。没有建议时写 `- none`。

如果没有任何可确认增量，仍然返回 accepted fact，并在 `description` 中明确写出空进展，例如：

```text
fallback_reason: unknown
confirmed_incremental_facts:
- 本轮未形成可确认 recon 增量；原因：execute 阶段没有留下可验证的新增观察。
evidence_refs:
- none
unconfirmed_or_discarded:
- none
next_safe_recon:
- none
```

## 强约束

- 不编造资产、端点、参数、账号权限、认证状态、服务版本或响应内容。
- 不把假设、计划、推测、可能存在的信息写成事实。
- 不把已有图结构中的旧事实伪装成本轮增量；除非它直接作为证据引用。
- 不输出没有证据支撑的漏洞结论或风险判断。
- 不因为 execute 阶段失败就补做探索；fallback 只负责整理已经确认的结果。

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
