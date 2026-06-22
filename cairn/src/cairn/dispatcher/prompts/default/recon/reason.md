# 任务

你将收到一份 recon 项目的 YAML 任务图快照。该项目用于收集和组织攻击面信息，供后续漏洞挖掘使用；它不是漏洞验证项目。

你需要先分析已有覆盖面，再决定是否提出新的 recon intents。不要在输出中展示分析过程。

# 输出要求

只返回一个原始 JSON 对象，不得输出 Markdown、解释、代码块或分析过程。JSON 必须合法，包括正确的引号转义。

返回形态只能是以下之一：

- 拒绝：{"accepted": false, "reason": "policy_refusal"}
- 创建 intents：{"accepted": true, "data": {"intents": [{"from": ["origin"], "auth_scope": "anonymous", "description": "[asset_discovery] ..."}, {"from": ["origin"], "auth_scope": "authenticated", "description": "[endpoint_sampling] ..."}]}}
- 稳定：{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
- noop：{"accepted": true, "data": {"decision": "noop", "intents": []}}

不要输出 `complete`。Reason validator 会拒绝任何 `complete` payload。

# 语言建议

- JSON 字段名、枚举值、模板变量和分类标签保持英文；不要把 `accepted`、`data`、`intents`、`description`、`auth_scope` 等协议字段改成中文。
- 人类可读内容建议优先使用简体中文，包括 `data.intents[*].description` 中的目标、入口、认证范围和去重边界；资产名、URL、路径、参数名、命令、漏洞缩写和 `[asset_discovery]` 等分类标签可以保留英文。

# 覆盖分析要求

创建任何 intent 前，必须基于图快照完成覆盖分析：

- 已有 facts 覆盖了哪些资产、端点、认证边界、参数、角色和用户数据入口。
- Open intents 正在覆盖哪些目标、入口、`auth_scope`、任务类型和 recon 方向。
- Concluded intents 已经探索过哪些方向，已经产生哪些事实，哪些方向被证明无效或低价值。
- Anonymous 与 authenticated 两条 recon 线是否失衡，是否长期只推进了其中一条线。
- 新 intent 是否和已有 fact、open intent 或 concluded intent 在目标、入口、`auth_scope`、任务类型上重复。

如果覆盖分析后没有明显的新方向，返回 `decision="no_new_high_value"` 且 `intents=[]`；不要为了推进而硬造宽泛 intent。

# Intent 分类

只允许创建 recon 类 intents。每个 intent 必须属于以下类型之一，且 `description` 必须以对应类型标签开头，并明确体现目标或入口、`auth_scope` 和去重边界：

- `[asset_discovery]`：发现或枚举同一 scope 内的公开资产、子域、服务或技术栈线索。
- `[endpoint_sampling]`：抽样确认端点、页面、API、跳转链或登录前后可达路径。
- `[auth_boundary_mapping]`：梳理匿名与登录态之间的认证入口、重定向、状态码和访问边界。
- `[parameter_inventory]`：收集端点、表单、API、查询参数、请求体字段和上传入口。
- `[role_boundary_mapping]`：梳理登录态角色、权限层级、菜单/API 可见性和角色间边界。
- `[candidate_surface_collection]`：整理疑似值得后续 vuln 项目验证的候选攻击面，但不验证漏洞。
- `[scope_clarification]`：澄清 scope、第三方资产、无关域名、环境边界或测试限制。
- `[noise_filtering]`：排除重复、不可达、无关、第三方托管或低信号入口。

禁止创建漏洞验证类 intents。不要规划利用链验证、越权验证、注入验证、XSS/CSRF/SSRF/RCE 验证、影响性证明、报告生成或 finding 操作。疑似漏洞只能整理为 `[candidate_surface_collection]` 或相关 recon fact，等待 snapshot 后在 vuln 项目中验证。

# 去重规则

- 如果 open intent 已覆盖相同目标、入口、`auth_scope` 和任务类型，不再创建。
- 如果 concluded intent 或已有 fact 已覆盖同一方向，只有当新 fact 明确引出更细分、未覆盖的目标或入口时才创建。
- 不要重复创建同一资产、同一端点、同一认证边界、同一参数集合、同一角色边界或同一候选攻击面的 intent。
- `description` 必须包含清晰的去重语义：任务类型、目标或入口、认证范围、要收集的 recon 证据。
- 不要使用“继续探索”“深入分析”“进一步测试”“扩大覆盖”“补充检查”等无法去重的泛化描述。
- 如果待处理 intents 已经覆盖有价值的下一步工作，返回 `decision="noop"` 且 `intents=[]`。

# 双线推进策略

Recon 固定维护 anonymous 和 authenticated 两条信息收集线。

- `auth_scope="anonymous"`：覆盖公开资产、未认证端点、公开参数、重定向、公开 API、登录入口和匿名认证边界。
- `auth_scope="authenticated"`：覆盖登录后页面/API、角色边界、session 数据入口、会话隔离现象、登录态菜单和登录态参数。
- 不要求每轮都强行同时创建两条线，但要避免长期只推进一条线。
- 如果图中只有 `origin` 且没有 open intents，必须同时创建一个基线 `anonymous` recon intent 和一个基线 `authenticated` recon intent。
- 初始基线 intent 应从 `origin` 出发，并分别建立匿名公开面 baseline 与登录态页面/API baseline。

# 规则

- 每个 intent 必须包含 `auth_scope`，值只能是 `anonymous` 或 `authenticated`。
- 每个 intent 的 `from` id 必须来自有效 facts。
- 在提出新的 intents 时，最多提出 {max_intents} 个高价值且互不重叠的探索方向。
- 如果 open intents 为空但图不是初始 `origin` 图，并且没有新的高价值 recon 工作，返回 `decision="no_new_high_value"` 且 `intents=[]`。
- 如果 open intents 非空且已经覆盖下一步工作，返回 `decision="noop"` 且 `intents=[]`。
- 不要输出 `complete`。

# 上下文

## 图结构

```
{graph_yaml}
```

## 有效 facts

```
{fact_ids}
```

## 待处理 Intents

```
{open_intents}
```
