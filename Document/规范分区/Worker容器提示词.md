# Worker容器提示词

## 模块边界

- `container/AGENTS.md` 是 Cairn worker 容器内代理的通用运行指令。
- `container/Dockerfile` 会把该文件复制为 `/home/kali/workspace/AGENTS.md` 和 `/home/kali/workspace/CLAUDE.md`，分别供 Codex/Claude 类 worker 读取。
- 任务级 JSON 输出契约仍由 `cairn/src/cairn/dispatcher/prompts/default/*/*.md` 控制，`container/AGENTS.md` 只提供环境、工具、安全边界、OOB 和授权凭据使用规则。

## 当前状态

- 已将 worker 环境说明重写为通用运行指令。
- 已明确公网 OOB/反连服务器 `43.159.131.239:22222` 与授权登录账号的使用场景。
- 已加入安全红线：不得爆破、不得修改目标文件或数据、不得影响目标业务可用性。
- 已要求长输出保存到 workspace 文件，并在结论中引用路径。

## 验收方式

- `container/AGENTS.md` 不应再包含 `未填写` 占位符。
- `container/Dockerfile` 应继续保留以下复制逻辑：
  - `COPY ./AGENTS.md /home/kali/workspace/AGENTS.md`
  - `COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md`
- 这是提示词和文档变更，不需要运行 Python 单元测试。
