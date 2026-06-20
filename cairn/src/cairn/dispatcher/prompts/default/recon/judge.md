# Task

Evaluate whether the recon graph is ready to fork into vulnerability mining. This judgement is ephemeral: do not create facts, intents, findings, or reports.

Return only one raw JSON object.

```json
{"accepted": true, "data": {"verdict": "ready", "score": 86, "recommended_action": "create_vuln_project", "checklist": {}, "blocking_gaps": [], "non_blocking_gaps": []}}
```

Valid verdicts are `ready`, `not_ready`, and `blocked`.

## Graph

```
{graph_yaml}
```
