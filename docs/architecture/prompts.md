# Prompt 体系

Dispatcher 通过 prompt group 加载任务模板，并在执行前替换占位符。默认 prompt 以项目类型拆分为 `recon` 和 `vuln`。

## 加载入口

Prompt loader 签名：

```python
load_prompt(group, name, project_kind)
```

渲染入口：

```python
render_prompt(template, values)
```

配置字段：

```yaml
runtime:
  prompt_group: default
```

## 默认目录布局

```text
cairn/src/cairn/dispatcher/prompts/default/
  recon/
    reason.md
    explore.md
    explore_conclude.md
    judge.md
    fork_seed.md
  vuln/
    reason.md
    explore.md
    explore_conclude.md
    report.md
```

`mock` prompt group 保持平铺目录，用于降低测试成本。

## 占位符契约

默认 prompt 的必要占位符由 `dispatcher/config.py` 校验。

通用：

- `reason.md`：`{graph_yaml}`、`{fact_ids}`、`{open_intents}`、`{max_intents}`。
- `explore.md`：`{graph_yaml}`、`{intent_id}`、`{intent_description}`。
- `explore_conclude.md`：`{graph_yaml}`、`{intent_id}`、`{intent_description}`。

默认 `recon`/`vuln` 额外约束：

- `explore.md` 和 `explore_conclude.md` 需要 `{auth_context}`。
- `vuln/report.md` 需要 `{graph_yaml}`、`{intent_id}`、`{intent_description}`。
- `recon/judge.md` 需要 `{graph_yaml}`。
- `recon/fork_seed.md` 需要 `{graph_yaml}`、`{max_seed_facts}`。

修改 prompt 时必须保持这些占位符存在。

## Graph Snapshot 输入

Dispatcher 不把完整 YAML 直接塞进 prompt 文本，而是把 graph snapshot 写入 worker 容器中的临时文件，再在 prompt 中引用该路径。

好处：

- 减少 prompt 体积和转义风险。
- 保留可复查输入。
- 避免 worker stdout 混入过长 graph 内容。

## Recon Prompt 策略

Recon prompt 的核心目标是功能理解优先：

1. 先建立页面/功能、用户动作和业务流程。
2. 再把 route/API、参数和资产挂到功能点下。
3. 区分 anonymous 和 authenticated 两条线路。
4. 记录候选攻击面，但不验证漏洞。
5. 不创建 findings 或 report intent。

Recon reason 初始图必须创建两条 baseline intents：

- `auth_scope="anonymous"`
- `auth_scope="authenticated"`

Recon explore 可写 `feature_surface` fact，用于表达功能面和 API 绑定。

## Vuln Prompt 策略

Vuln prompt 负责漏洞验证：

- 从 recon snapshot 和 seed facts 继续验证。
- 对已有 findings 做去重与派生检查。
- 已有 finding 不代表同一漏洞机制完全收敛。
- 新 token 来源、接收方、接口族、最小条件矩阵或影响面补强可形成窄范围 follow-up。
- 禁止“继续测试”“深入挖掘”等无明确新增维度的泛化 intent。

Vuln explore 在 finding 成立时应判断：

- 是否需要 `follow_up`。
- 是否可以进入 `report`。
- 是否因为前置条件、token、现网回摆或超时导致未完成，需要记录已完成/未完成矩阵。

## Judge Prompt 策略

Judge 是 recon readiness judgement，不写 graph，不代表 project completed。

Judge 输出 checklist 固定包含：

- `scope_clarity`
- `feature_coverage`
- `feature_api_mapping_quality`
- `auth_boundary_coverage`
- `candidate_surface_quality`

每项包含 0-20 的 `score` 和 `evidence`。总分建议为五项相加，0-100。

## Golden Examples

Recon prompt fixture 位于：

```text
cairn/tests/fixtures/prompts/recon/
```

当前 fixture 目的：

- `initial_origin.yaml`：只有 `origin`，reason 应创建 anonymous/authenticated baseline intents。
- `with_open_intents.yaml`：已有 open intents，reason 应避免重复。
- `ready_for_judge.yaml`：包含足够 facts，judge 应输出可解释 checklist。

测试文件：

```text
cairn/tests/test_recon_prompt_fixtures.py
```

该测试不调用真实模型，只检查 YAML 形态、必要字段、示例域名和 secret-looking 文本。

## 修改 Prompt 后的验证

推荐命令：

```bash
cd cairn
uv run --group dev pytest -s tests/test_prompt_contracts.py tests/test_recon_prompt_fixtures.py
```

涉及 JSON contract 时追加：

```bash
cd cairn
uv run --group dev pytest -s tests/test_contracts_and_drivers.py tests/test_worker_tasks.py
```

全量验证：

```bash
cd cairn
uv run --group dev pytest -s
```

## 语言策略

- 默认 prompt 建议 worker 的人类可读内容优先使用简体中文。
- JSON 字段名、枚举值、模板变量和 fenced code 结构保持英文。
- 技术术语、URL、路径、参数、payload、PoC、CVE/CWE、漏洞缩写可以保留英文。
- 不因英文内容判失败，也不做运行时中文比例校验。
