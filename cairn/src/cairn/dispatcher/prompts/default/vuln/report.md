# Task

Draft an SRC submission report for the Current Report Intent. Use the graph snapshot and the referenced finding context. Return a concise Markdown report and optional structured metadata.

Return only one raw JSON object.

```json
{"accepted": true, "data": {"report_markdown": "# Title\n\n...", "report_json": {}}}
```

If rejected:

```json
{"accepted": false, "reason": "policy_refusal"}
```

## Graph

```
{graph_yaml}
```

## Current Intent

```
{intent_id}
```

## Current Intent Description

```
{intent_description}
```
