# 任务

你处于 collection E worker 的 conclude fallback 阶段。不要继续探索，只整理当前会话已经确认的信息，产出一个 `collection_fact`。

# 输出要求

只返回一个原始 JSON 对象。`data` 必须包含 `description` 和 `evidence`：

```json
{"accepted": true, "data": {"description": "...", "evidence": "/home/kali/evidence/t1-conclude.json"}}
```

如果拒绝：

```json
{"accepted": false, "reason": "policy_refusal"}
```

不要输出 `findings`、`report`、`complete`、`tasks` 或解释文字。

# 规则

- 立即停止新命令、新访问、新登录和额外验证。
- 只总结当前 task 已确认的增量事实。
- `evidence` 指向已写入的证据文件，文件应包含步骤、观察、失败原因和复现说明。
- 不编造资产、端点、权限、服务版本、响应内容或漏洞结论。

# 上下文

## 图结构

```
{graph_yaml}
```

## 认证上下文

```
{auth_context}
```

## 当前 Task

```
{task_id}
```

## 当前 Task 说明

```
{task_description}
```
