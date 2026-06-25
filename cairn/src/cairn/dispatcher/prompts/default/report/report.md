# 任务

为 Current Report Intent 起草一份 SRC 提交报告。使用图快照和被引用的 finding 上下文，返回一份简洁的 Markdown 报告和可选的结构化 metadata。

只返回一个原始 JSON 对象。

```json
{"accepted": true, "data": {"report_markdown": "# Title\n\n...", "report_json": {}}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## 图结构

```
{graph_yaml}
```

## 当前 Intent

```
{intent_id}
```

## 当前 Intent 说明

```
{intent_description}
```
