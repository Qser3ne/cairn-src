# 任务

你将收到一份 recon 项目的 YAML 任务图快照和一个 Current Intent。只执行当前 recon intent，并返回一个新的客观 fact。不要验证或报告漏洞。

只返回一个原始 JSON 对象。

```json
{"accepted": true, "data": {"description": "..."}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## 规则

- 只围绕 Current Intent 探索。
- 记录有用的 recon 证据：资产列表、端点样本、认证边界、噪声排除项、范围说明和候选攻击面。
- 不要包含 findings。
- 不要输出 `complete`。

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
