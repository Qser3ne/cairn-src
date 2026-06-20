# Task

You will receive a YAML graph snapshot and a Current Intent for a recon project. Execute only the current recon intent and return one new objective fact. Do not validate or report vulnerabilities.

Return only one raw JSON object.

```json
{"accepted": true, "data": {"description": "..."}}
```

If rejected:

```json
{"accepted": false, "reason": "policy_refusal"}
```

## Rules

- Stay within the Current Intent.
- Record useful recon evidence: asset lists, endpoint samples, auth boundaries, noise exclusions, scope notes, and candidate attack surfaces.
- Do not include findings.
- Do not output `complete`.

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
