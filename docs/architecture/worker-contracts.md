# Worker JSON 契约

Dispatcher 只接受结构化 JSON 输出。自然语言可以用于人类可读字段，但协议字段名、枚举值和模板变量必须保持英文。

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

输入占位符：

- `{graph_yaml}`
- `{fact_ids}`
- `{open_intents}`
- `{max_intents}`

Recon reason 还要求每个 intent 明确 `auth_scope`。

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "intents": [
      {
        "from": ["origin"],
        "description": "[feature_mapping] 梳理匿名首页可见功能和入口",
        "auth_scope": "anonymous"
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
- 初始 recon 至少需要 anonymous 和 authenticated 两条 baseline intent；Dispatcher 会把初始 max intents 提升到至少 2。
- 重复 intent 返回 `409` 时跳过，不把 reason 任务视为失败。
- Recon `noop` 和 `no_new_high_value` 都会记录 reason round；`no_new_high_value` 记为 stable。

## Explore

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

Vuln explore 可以附带 findings：

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
- Recon explore 越界返回 `findings` 时，Dispatcher 写回前丢弃 findings。
- Vuln explore 可把合法 findings 传给 Server。
- Execute 超时或非 JSON 时，如 worker adapter 支持 session，Dispatcher 可运行 `explore_conclude` fallback。

## Judge

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

写回规则：

- 成功调用 `/ephemeral-jobs/{job_id}/finish`。
- 失败调用 `/ephemeral-jobs/{job_id}/fail`。
- 对 judge jobs，`ready|not_ready|blocked` 会更新 project `judge_status/judged_at`。
- 不创建 facts、intents、findings 或 reports。

## Fork Seed

Fork seed 是 ephemeral job，用于从 recon snapshot 生成 child vuln seed facts。

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

- Dispatcher 调用 `/ephemeral-jobs/{job_id}/finish-fork-seed`。
- Server 原子创建 child vuln project。
- Parent recon graph 不写入漏洞验证结果。

## Report

Report 消费 `intent_kind="report"` 的 intent。

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
