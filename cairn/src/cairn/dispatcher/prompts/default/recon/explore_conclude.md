# Task

You are in conclude fallback for a recon project. Stop exploring and summarize only the confirmed incremental recon result for the Current Intent.

Return only one raw JSON object.

```json
{"accepted": true, "data": {"description": "..."}}
```

If rejected:

```json
{"accepted": false, "reason": "policy_refusal"}
```

## Rules

- Do not include findings.
- Do not output `complete`.
- Do not invent unverified assets or endpoints.

## Graph

```
{graph_yaml}
```

## Auth Context

```
{auth_context}
```

## Current Intent

```
{intent_id}
```

## Current Intent Description

```
{intent_description}
```
