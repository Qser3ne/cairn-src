# 任务

评估 recon 图是否已经可以 fork 为漏洞挖掘项目。该判断是临时性的：不要创建 facts、intents、findings 或 reports。

只返回一个原始 JSON 对象。

```json
{"accepted": true, "data": {"verdict": "ready", "score": 86, "recommended_action": "create_vuln_project", "checklist": {}, "blocking_gaps": [], "non_blocking_gaps": []}}
```

有效的 verdict 值为 `ready`、`not_ready` 和 `blocked`。

## 图结构

```
{graph_yaml}
```
