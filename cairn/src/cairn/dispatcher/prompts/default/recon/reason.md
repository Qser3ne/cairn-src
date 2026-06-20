# Task

You will receive a YAML graph snapshot for a recon project. This project collects and organizes attack-surface information for later vulnerability mining. It is not a vulnerability validation project.

Return only one raw JSON object.

If you must reject, return:

```json
{"accepted": false, "reason": "policy_refusal"}
```

When creating new recon intents:

```json
{"accepted": true, "data": {"intents": [{"from": ["origin"], "description": "..."}]}}
```

When no new high-value recon work remains:

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

## Rules

- Do not create intents that verify a vulnerability.
- Only create asset discovery, endpoint sampling, auth boundary mapping, attack-surface candidate collection, scope clarification, or noise-filtering intents.
- Do not output `complete`.
- Use at most {max_intents} intents.
- Every `from` id must come from Valid facts and must not include `goal`.
- If Open Intents already cover the useful next work, return `decision="noop"` with empty intents.

## Graph

```
{graph_yaml}
```

## Valid facts

```
{fact_ids}
```

## Open Intents

```
{open_intents}
```
