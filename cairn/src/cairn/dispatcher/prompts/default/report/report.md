# 任务

为当前 finding 起草一份 SRC 提交报告。使用图快照和 finding 上下文，返回报告文件路径。

只返回一个原始 JSON 对象。

```json
{"accepted": true, "data": {"report": "/home/kali/reports/F1.md"}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

## 图结构

```
{graph_yaml}
```

## 当前 Finding

```
{finding_id}
```

## 当前 Finding 说明

```
{finding_description}
```
