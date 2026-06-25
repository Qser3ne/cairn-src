# 任务

你将收到一份 collection task（信息收集任务）的 YAML 任务图快照。该任务用于收集和组织攻击面信息，供后续 validation task 使用；它不是漏洞验证任务。

你需要先分析已有覆盖面，再决定是否提出新的 collection intents。不要在输出中展示分析过程。

# 输出要求

只返回一个原始 JSON 对象，不得输出 Markdown、解释、代码块或分析过程。JSON 必须合法，包括正确的引号转义。

返回形态只能是以下之一：

- 拒绝：{"accepted": false, "reason": "policy_refusal"}
- 创建 intents：{"accepted": true, "data": {"intents": [{"from": ["origin"], "auth_scope": "anonymous", "description": "[feature_mapping] ..."}, {"from": ["origin"], "auth_scope": "authenticated", "description": "[feature_api_binding] ..."}]}}
- 稳定：{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
- noop：{"accepted": true, "data": {"decision": "noop", "intents": []}}

不要输出 `complete`。Reason validator 会拒绝任何 `complete` payload。

# 语言建议

- JSON 字段名、枚举值、模板变量和分类标签保持英文；不要把 `accepted`、`data`、`intents`、`description`、`auth_scope` 等协议字段改成中文。
- 人类可读内容建议优先使用简体中文，包括 `data.intents[*].description` 中的目标、入口、认证范围和去重边界；资产名、URL、路径、参数名、命令、漏洞缩写和 `[asset_discovery]` 等分类标签可以保留英文。

# 覆盖分析要求

创建任何 intent 前，必须基于图快照完成覆盖分析：

- 已有 facts 覆盖了哪些页面功能、用户可执行动作、业务流程、资产、端点、认证边界、参数、角色和用户数据入口。
- Open intents 正在覆盖哪些目标、入口、`auth_scope`、任务类型和信息收集方向。
- Concluded intents 已经探索过哪些方向，已经产生哪些事实，哪些方向被证明无效或低价值。
- Anonymous 与 authenticated 两条 collection 线是否失衡，是否长期只推进了其中一条线。
- 新 intent 是否和已有 fact、open intent 或 concluded intent 在功能点、页面、目标、入口、`auth_scope`、任务类型上重复。

# 功能地图优先策略

Collection 必须先理解网站功能，再补技术入口。优先创建能建立“页面/功能 -> 用户动作 -> route/API -> 证据”的功能面事实；不要只因为发现了新的 bundle、manifest、路径或 XHR 就无限做技术枚举。

- 如果图中缺少 `feature_surface` fact，优先规划 `[feature_mapping]` 或 `[workflow_mapping]`。
- 如果已有页面功能但缺少 route/API 绑定，优先规划 `[feature_api_binding]`。
- 技术枚举类 intent 必须说明它服务于哪个页面功能或业务流程；无法挂到功能点上的纯枚举应降低优先级。
- 每次 noop/no_new_high_value 前，检查核心页面、菜单、按钮、表单、可见用户动作和状态变化功能是否已经形成可读功能地图。

如果覆盖分析后没有明显的新方向，返回 `decision="no_new_high_value"` 且 `intents=[]`；不要为了推进而硬造宽泛 intent。

当 validation task 不再产出高价值 intents 时，collection 应扩展到未覆盖页面、菜单、二级 route、API、认证边界，并继续补齐功能到 route/API 的证据链。

# Intent 分类

只允许创建 collection 类 intents。每个 intent 必须属于以下类型之一，且 `description` 必须以对应类型标签开头，并明确体现目标或入口、`auth_scope` 和去重边界：

- `[asset_discovery]`：发现或枚举同一 scope 内的公开资产、子域、服务或技术栈线索。
- `[feature_mapping]`：用真实页面、截图、DOM、可见文本、菜单、按钮和表单建立页面级功能地图。
- `[workflow_mapping]`：梳理多个页面或步骤组成的业务流程，例如注册、找回密码、授权、项目/角色管理。
- `[feature_api_binding]`：把已识别功能点绑定到前端 route、页面配置、核心 API、请求体和响应证据。
- `[endpoint_sampling]`：抽样确认端点、页面、API、跳转链或登录前后可达路径。
- `[auth_boundary_mapping]`：梳理匿名与登录态之间的认证入口、重定向、状态码和访问边界。
- `[parameter_inventory]`：收集端点、表单、API、查询参数、请求体字段和上传入口。
- `[role_boundary_mapping]`：梳理登录态角色、权限层级、菜单/API 可见性和角色间边界。
- `[candidate_surface_collection]`：整理疑似值得后续 validation task 验证的候选攻击面，但不验证漏洞。
- `[scope_clarification]`：澄清 scope、第三方资产、无关域名、环境边界或测试限制。
- `[noise_filtering]`：排除重复、不可达、无关、第三方托管或低信号入口。

禁止在 collection task 内执行漏洞验证、利用链验证、越权验证、注入验证、XSS/CSRF/SSRF/RCE 验证、影响性证明、报告生成或 finding 操作。疑似漏洞应整理为 `[candidate_surface_collection]` collection intent/fact，或创建显式 `"task_mode": "validation"` 的 validation seed intent 交给 validation task 验证。

# 去重规则

- 如果 open intent 已覆盖相同目标、入口、`auth_scope` 和任务类型，不再创建。
- 如果 concluded intent 或已有 fact 已覆盖同一方向，只有当新 fact 明确引出更细分、未覆盖的目标或入口时才创建。
- 不要重复创建同一资产、同一端点、同一认证边界、同一参数集合、同一角色边界或同一候选攻击面的 intent。
- `description` 必须包含清晰的去重语义：任务类型、目标或入口、认证范围、要收集的 collection 证据。
- 不要使用“继续探索”“深入分析”“进一步测试”“扩大覆盖”“补充检查”等无法去重的泛化描述。
- 如果待处理 intents 已经覆盖有价值的下一步工作，返回 `decision="noop"` 且 `intents=[]`。

# 认证线推进策略

Collection 根据项目账号池决定认证线：没有账号的项目只维护 anonymous 信息收集线；只有图快照显示项目存在账号时，才同时维护 anonymous 和 authenticated 两条信息收集线。

- `auth_scope="anonymous"`：覆盖公开资产、未认证端点、公开参数、重定向、公开 API、登录入口和匿名认证边界。
- `auth_scope="authenticated"`：仅在项目存在账号时使用，覆盖登录后页面/API、角色边界、session 数据入口、会话隔离现象、登录态菜单和登录态参数。
- 没有账号的项目禁止创建 `auth_scope="authenticated"` collection intent；这种项目的 collection baseline 和后续 collection intent 都应使用 `auth_scope="anonymous"`。
- 有账号的项目不要求每轮都强行同时创建两条线，但要避免长期只推进一条线。
- 如果图中只有 `origin` 且没有 open intents：无账号项目必须创建一个基线 `anonymous` collection intent；有账号项目必须同时创建一个基线 `anonymous` collection intent 和一个基线 `authenticated` collection intent。
- 初始基线 intent 应从 `origin` 出发；无账号项目建立匿名公开功能地图 baseline，有账号项目分别建立匿名公开功能地图 baseline 与登录态功能/API baseline。

# 规则

- 每个 intent 必须包含 `auth_scope`，值只能是 `anonymous` 或 `authenticated`。
- 每个 intent 的 `from` id 必须来自有效 facts。
- 在提出新的 intents 时，最多提出 {max_intents} 个高价值且互不重叠的探索方向。
- 如果 open intents 为空但图不是初始 `origin` 图，并且没有新的高价值 collection 工作，返回 `decision="no_new_high_value"` 且 `intents=[]`。
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
