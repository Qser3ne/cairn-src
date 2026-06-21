# 任务

你现在处于 recon 项目的 conclude fallback 阶段。停止探索，只总结 Current Intent 已确认的增量 recon 结果。

只返回一个原始 JSON 对象。

```json
{"accepted": true, "data": {"description": "..."}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## 规则

- 不要包含 findings。
- 不要输出 `complete`。
- 不要编造未验证的资产或端点。

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
