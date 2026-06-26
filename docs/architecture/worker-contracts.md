# Worker JSON 契约

Dispatcher 只接受结构化 JSON 输出。自然语言可以用于人类可读字段，但协议字段名、枚举值和模板变量必须保持英文。

契约校验失败会使当前 task/job 失败，而不是把不完整 payload 写回 Server。新 prompt 和 mock adapter 都应输出完整契约字段。

## 通用输出包装

推荐输出：

```json
{"accepted": true, "data": {}}
```

拒绝输出：

```json
{"accepted": false, "reason": "policy_refusal"}
```

Dispatcher 兼容部分未包装的 legacy payload，但新 prompt 应优先使用 `accepted/data` 包装。

## Reason

Active reason task types are `collection_reason` and `validation_reason`.

输入占位符：

- `{graph_yaml}`
- `{fact_ids}`
- `{open_intents}`
- `{max_intents}`

Renderer context 会提供 `{task_mode}` 给 prompt 模板使用；它不是默认 reason prompt 的必需 token，除非具体 prompt group/template 实际引用它。

Reason contract 按 `task_mode` 校验输出：

- `collection` reason 默认把输出 intent 归一化为 `task_mode="collection"`，并要求 collection intent 明确 `auth_scope`。
- `collection` reason 可以创建显式 `task_mode="validation"` 的 validation seed intent，但 description 必须体现 validation focus。
- `validation` reason 默认把输出 intent 归一化为 `task_mode="validation"`，不要求 collection taxonomy 或 `auth_scope`。
- 只有缺少 `task_mode` key 时才按 reason mode 默认归一化；显式提供的 `task_mode` 必须精确合法，未知值或 `""`、`null`、`false`、`0` 等 falsy 值都会被拒绝。

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "intents": [
      {
        "from": ["origin"],
        "description": "[feature_mapping] 梳理匿名首页可见功能和入口",
        "auth_scope": "anonymous",
        "task_mode": "collection"
      }
    ]
  }
}
```

No-op 输出：

```json
{"accepted": true, "data": {"decision": "noop", "intents": []}}
```

Stable 输出：

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

非法输出：

```json
{"accepted": true, "data": {"complete": {"description": "done"}}}
```

写回规则：

- `intents` 最多创建 `tasks.reason.max_intents` 个。
- 初始 collection 需要覆盖项目账号池允许的 baseline collection intents；无 accounts 项目只要求 anonymous，有 accounts 项目要求 anonymous 和 authenticated 两条，Dispatcher 会按需要提升初始 max intents。
- 重复 intent 返回 `409` 时跳过，不把 reason 任务视为失败。
- 除项目停止/租约冲突类 `403`、`409` 外，intent 创建或 collection reason round 写回失败会让 reason task 返回 `failed`，避免 scheduler 推进 checkpoint 后丢失模型输出；如果写回期间同时观察到 heartbeat lease failure，`403`/`409` 分支也返回 `failed`。
- Collection `noop` 和 `no_new_high_value` 都会记录 reason round；`no_new_high_value` 记为 stable。

## Explore

Active explore task types are `collection_explore` and `validation_explore`.

Explore contract 按 intent `task_mode` 校验输出：`collection` 只允许写 facts，不允许写 findings；`validation` 可以在合法 findings 字段完整时写 findings。

输入占位符：

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`
- `{auth_context}`

普通 fact 输出：

```json
{
  "accepted": true,
  "data": {
    "description": "已确认登录页存在账号密码登录入口，提交到 POST /api/login。"
  }
}
```

功能面 fact 输出：

```json
{
  "accepted": true,
  "data": {
    "description": "登录页提供账号密码登录和找回密码入口。",
    "fact_type": "feature_surface",
    "title": "登录功能面",
    "summary": "用户可以提交账号密码或跳转找回密码。",
    "details": {
      "user_actions": ["提交账号密码", "跳转找回密码"],
      "routes": ["/login"],
      "apis": ["POST /api/login"],
      "evidence_refs": ["/home/kali/evidence/login-summary.txt"]
    }
  }
}
```

Validation explore 可以附带 findings：

```json
{
  "accepted": true,
  "data": {
    "description": "已验证订单详情接口存在越权读取风险。",
    "findings": [
      {
        "title": "订单详情 IDOR",
        "vulnerability_type": "idor",
        "severity": "high",
        "target": "https://target.example",
        "location": "/api/orders/{id}",
        "impact": "攻击者可读取其他用户订单详情。",
        "evidence": "见 /home/kali/evidence/order-idor-summary.txt。",
        "reproduction": "使用账号 A 登录后请求账号 B 的订单 ID。",
        "remediation": "服务端按当前用户校验订单归属。",
        "status": "open",
        "research_value": "high",
        "next_action": "report",
        "followup_reason": "",
        "followup_intent_description": ""
      }
    ]
  }
}
```

写回规则：

- Dispatcher 启动 worker 前先 heartbeat claim intent。
- 成功后调用 `/intents/{intent_id}/conclude`。
- Collection explore 越界返回 `findings` 时，Dispatcher 写回前丢弃 findings；合同层直接校验 collection payload 时会拒绝带 findings 的输出。
- Server 也会拒绝 collection conclude 请求中的 `findings`，防止绕过 Dispatcher 写入漏洞验证结果。
- Validation explore 可把合法 findings 传给 Server；finding 必须包含完整必填字段，枚举字段必须取合法值。
- Execute 超时或非 JSON 时，如 worker adapter 支持 session，Dispatcher 可运行 `explore_conclude` fallback。

## Retired Legacy Judge

Judge 是 retired legacy ephemeral job contract，只用于旧数据、旧 queued jobs 和迁移测试说明；它不是当前 collection/validation/report 主流程的一部分。

Judge 是 ephemeral job，不 claim project reason lease，不写 graph。

输入占位符：

- `{graph_yaml}`

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "verdict": "ready",
    "score": 86,
    "recommended_action": "create_vuln_project",
    "checklist": {
      "scope_clarity": {"score": 18, "evidence": "授权范围和 origin 清晰。"},
      "feature_coverage": {"score": 17, "evidence": "主要页面功能已有事实覆盖。"},
      "feature_api_mapping_quality": {"score": 16, "evidence": "关键功能已绑定 route/API。"},
      "auth_boundary_coverage": {"score": 18, "evidence": "匿名和登录态边界已分离。"},
      "candidate_surface_quality": {"score": 17, "evidence": "候选攻击面具体，可进入漏洞验证。"}
    },
    "blocking_gaps": [],
    "non_blocking_gaps": []
  }
}
```

合法 `verdict`：

- `ready`
- `not_ready`
- `blocked`

合法 `recommended_action`：

- `create_vuln_project`
- `continue_anonymous_recon`
- `continue_authenticated_recon`
- `clarify_scope`
- `fix_account_access`
- `stop_or_archive`

Judge 校验规则：

- `score` 和每个 checklist `score` 必须是整数，不能是布尔值或浮点数。
- checklist 必须包含 `scope_clarity`、`feature_coverage`、`feature_api_mapping_quality`、`auth_boundary_coverage`、`candidate_surface_quality` 五个固定 key。
- 每个 checklist 项都要提供 `score` 和非空 `evidence`。
- `blocking_gaps` 和 `non_blocking_gaps` 必须是数组。

写回规则：

- 成功调用 `/ephemeral-jobs/{job_id}/finish`。
- 失败调用 `/ephemeral-jobs/{job_id}/fail`。
- 对 judge jobs，`ready|not_ready|blocked` 会更新 project `judge_status/judged_at`。
- 不创建 facts、intents、findings 或 reports。

## Retired Legacy Fork Seed

Fork seed 是 retired legacy ephemeral job contract，只用于旧 `recon -> snapshot -> fork vuln` 数据或迁移测试说明；它不是当前主流程的一部分。当前主流程使用 collection facts 和 validation seed intents 进入 validation。

Fork seed 历史上是 ephemeral job，用于从 recon snapshot 生成 child vuln seed facts。

输出要求：

- `seed_facts` 非空。
- 数量受 `tasks.fork_seed.max_seed_facts` 限制。
- 每个 seed fact 必须包含 `title`、`auth_scope`、`candidate_type`、`derived_from`、`description`。
- `derived_from` 必须引用 snapshot YAML 中存在的 recon fact ID。
- 可选数组字段缺省规范为 `[]`。

示例：

```json
{
  "accepted": true,
  "data": {
    "seed_facts": [
      {
        "title": "匿名登录功能验证种子",
        "auth_scope": "anonymous",
        "candidate_type": "feature_surface",
        "derived_from": ["f006", "f008"],
        "feature_summary": "登录页提供账号密码登录和找回密码入口。",
        "user_actions": ["提交账号密码", "跳转找回密码"],
        "routes": ["/login"],
        "apis": ["POST /api/login"],
        "vuln_validation_focus": ["认证边界", "错误处理"],
        "known_constraints": ["anonymous only"],
        "evidence_refs": ["/home/kali/evidence/login-summary.txt"],
        "description": "围绕匿名登录功能验证认证边界与错误处理。"
      }
    ]
  }
}
```

写回规则：

- Legacy Dispatcher 调用 `/ephemeral-jobs/{job_id}/finish-fork-seed`。
- Legacy Server 原子创建 child vuln project。
- Parent recon graph 不写入漏洞验证结果。

## Report

Report 消费 `intent_kind="report"`、`task_mode="report"` 的 intent。

输入占位符：

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "report_markdown": "# 订单详情 IDOR\n\n## 影响\n攻击者可读取其他用户订单详情。",
    "report_json": {
      "severity": "high",
      "finding_id": "v001"
    }
  }
}
```

写回规则：

- Dispatcher heartbeat claim report intent。
- 成功调用 `/intents/{intent_id}/report`。
- Server 创建 `finding_reports`。
- Server 设置 finding `report_status="drafted"`。
- Report 不创建新 fact。

## 输出语言策略

- 协议字段名、枚举值、模板变量保持英文。
- 人类可读字段建议优先使用简体中文。
- URL、路径、参数、payload、CVE/CWE、漏洞缩写、命令和证据路径保持原样。
- 当前没有运行时中文比例校验或自动翻译。
