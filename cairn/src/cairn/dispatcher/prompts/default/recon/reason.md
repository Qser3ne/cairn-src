# Task

You will receive a YAML graph snapshot for a recon project. This project collects and organizes attack-surface information for later vulnerability mining. It is not a vulnerability validation project.

Return only one raw JSON object.

If you must reject, return:

```json
{"accepted": false, "reason": "policy_refusal"}
```

When creating new recon intents:

```json
{"accepted": true, "data": {"intents": [{"from": ["origin"], "auth_scope": "anonymous", "description": "..."}, {"from": ["origin"], "auth_scope": "authenticated", "description": "..."}]}}
```

When no new high-value recon work remains:

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

## Rules

- Do not create intents that verify a vulnerability.
- Only create asset discovery, endpoint sampling, auth boundary mapping, attack-surface candidate collection, scope clarification, or noise-filtering intents.
- Every intent must include `auth_scope`, either `anonymous` for unauthenticated recon or `authenticated` for logged-in recon.
- If the graph only has `origin` and no open intents, create one baseline `anonymous` recon intent and one baseline `authenticated` recon intent.
- Keep both main lines alive over time: unauthenticated recon covers public assets, unauthenticated endpoints, parameters, and auth boundaries; authenticated recon covers logged-in pages, APIs, permission boundaries, and user-data entry points.
- Do not output `complete`.
- Use at most {max_intents} intents.
- Every `from` id must come from Valid facts.
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
