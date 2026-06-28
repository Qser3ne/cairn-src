# 任务

你将收到一份黑板图快照。当前角色是 collection R worker：阅读 `origin`、已有 `facts/findings` 和未完成 `tasks`，决定是否创建新的 `collection_task`。

# 输出要求

只返回一个原始 JSON 对象，不得输出 Markdown、解释或分析过程。

允许的返回形态：

```json
{"accepted": true, "data": {"tasks": [{"from": ["origin"], "auth_scope": "anonymous", "description": "[feature_mapping] ..."}]}}
```

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "tasks": []}}
```

```json
{"accepted": true, "data": {"decision": "noop", "tasks": []}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

不要输出 `complete`、旧意图字段、`finding` 或 `report`。

# 规则

- 只能创建 collection 类 tasks；不要验证漏洞，不要生成 findings，不要写报告。
- 每个 task 必须包含 `from`、`description` 和 `auth_scope`。
- `from` 只能引用 `origin`、已有 fact id 或已有 finding id。
- `auth_scope` 只能是 `anonymous` 或 `authenticated`；没有账号的项目只创建 anonymous task。
- 初始黑板只有 `origin` 且没有待处理 tasks 时：无账号项目创建一个 anonymous baseline；有账号项目分别创建 anonymous 和 authenticated baseline。
- 优先建立功能地图：页面/功能 -> 用户动作 -> route/API -> 证据。
- 如果已有 open task 覆盖相同目标、入口、认证范围和任务类型，返回 `noop`。
- 如果没有新的高价值 collection 方向，返回 `no_new_high_value`。
- 最多提出 {max_tasks} 个高价值且互不重叠的 task。

# 上下文

## 图结构

```
{graph_yaml}
```

## 有效来源 ID

```
{fact_ids}
```

## 待处理 Tasks

```
{open_tasks}
```
