# Recon Prompt Golden Examples

## 目标

`cairn/tests/fixtures/prompts/recon/` 保存 recon prompt 的静态 golden examples。它们不是模型测试，不调用真实 LLM，只提供可读的输入图样例和人工/自动检查输出质量的基准。

## Fixture

- `initial_origin.yaml`：只有 `origin` fact，没有 intents。Reason 看到该图时应创建两条 baseline recon intents：一条 `auth_scope="anonymous"`，一条 `auth_scope="authenticated"`；不得输出 `complete`。
- `with_open_intents.yaml`：已有 anonymous 与 authenticated 两条 open intents。Reason 应识别当前下一步已被覆盖，优先返回 `noop` 或 `no_new_high_value`，不得重复创建相同目标、入口、`auth_scope` 和任务类型的 intent。
- `ready_for_judge.yaml`：包含资产、端点、认证边界和候选攻击面 facts。Judge 应输出可解释 checklist，并给出 `ready` 或 `not_ready`；不得写 facts、intents、findings、reports，也不得把判断当成 project completed。

## 自动验收

`cairn/tests/test_recon_prompt_fixtures.py` 只检查：

- YAML 能被 `yaml.safe_load()` 解析。
- `project`、`recon`、`facts`、`intents` 等必要字段存在且形态符合场景。
- fixture 不包含真实目标、真实账号、真实 token 或 secret-looking 文本。
- fixture 域名固定使用 `.example.test`。

## 使用方式

修改 `cairn/src/cairn/dispatcher/prompts/default/recon/*.md` 后，人工把对应 fixture 作为 graph 输入检查输出形态。若未来接入离线 prompt renderer 或 mock evaluator，可以直接复用这些 fixture，但仍不应在单元测试里调用 live model。
