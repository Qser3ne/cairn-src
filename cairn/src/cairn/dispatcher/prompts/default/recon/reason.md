# 任务

你将收到一份 recon 项目的 YAML 任务图快照。该项目用于收集和组织攻击面信息，供后续漏洞挖掘使用；它不是漏洞验证项目。

只返回一个原始 JSON 对象。

如果必须拒绝，返回：

```json
{"accepted": false, "reason": "policy_refusal"}
```

需要创建新的 recon intents 时，返回：

```json
{"accepted": true, "data": {"intents": [{"from": ["origin"], "auth_scope": "anonymous", "description": "..."}, {"from": ["origin"], "auth_scope": "authenticated", "description": "..."}]}}
```

当没有新的高价值 recon 工作时，返回：

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

## 规则

- 不要创建用于验证漏洞的 intents。
- 只创建资产发现、端点抽样、认证边界映射、攻击面候选收集、范围澄清或噪声过滤类 intents。
- 每个 intent 必须包含 `auth_scope`，值为 `anonymous`（未认证 recon）或 `authenticated`（登录态 recon）。
- 如果图中只有 `origin` 且没有 open intents，则创建一个基线 `anonymous` recon intent 和一个基线 `authenticated` recon intent。
- 随着时间推移保持两条主线都持续推进：未认证 recon 覆盖公开资产、未认证端点、参数和认证边界；登录态 recon 覆盖登录后页面、API、权限边界和用户数据入口。
- 不要输出 `complete`。
- 最多使用 {max_intents} 个 intents。
- 每个 `from` id 必须来自有效 facts。
- 如果待处理 Intents 已经覆盖有价值的下一步工作，返回 `decision="noop"` 且 intents 为空。

## 图结构

```
{graph_yaml}
```

## 有效 facts

```
{fact_ids}
```

## 待处理 Intents

```
{open_intents}
```
