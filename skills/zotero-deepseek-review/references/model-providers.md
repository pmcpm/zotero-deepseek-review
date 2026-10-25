# 模型渠道与任务配置

读取时机：准备模型调用、切换渠道/模型、诊断模型发现或推理错误。

## 默认行为

任务的 `model` 是真实模型 ID。`model_api` 缺省时按 `{"provider":"cstcloud"}` 处理，新任务初始化同样采用这一默认值；默认模型为 `deepseek-v4-flash`。本机和当前项目沿用 CSTCloud，已有任务不做迁移。

`cstcloud` 适配器调用单独安装的 `cstcloud-model-api/scripts/cstcloud_api.py`，从 `$CODEX_HOME/skills`（缺省 `~/.codex/skills`）定位。必须读取该 skill 并沿用其 DPAPI 密钥池/环境变量回退、直连、授权模型、诊断及有界重试。该适配器保持原有请求：thinking on、temperature 0.3、输出上限 `min(16000,max(2048,word_limit*5))`、超时 240 秒、transport retries 1。`model_api` 对该路径仅设置 provider，其他选项由依赖 skill 管理。

## OpenAI 兼容渠道

用户选择其他渠道或模型时设置 `provider: "openai_compatible"`。模型必须支持中文写作、system/user 消息与非流式 Chat Completions 返回形状。供应商原生消息 API 需通过兼容网关或另写适配器。

| 字段 | 默认值/含义 |
| --- | --- |
| `base_url` | 必填，完整 API 前缀，如 `https://host.example/v1`；其后拼接 `/models`、`/chat/completions` |
| `api_key_env` | `REVIEW_MODEL_API_KEY`；仅保存环境变量名称。回环无鉴权服务可显式为 null |
| `discover_models` | true；列表须包含 task.model。渠道无模型列表接口时由用户明确设置 false，并记录未完成发现探测 |
| `trust_env` | false，忽略环境代理；true 使用 Python urllib 环境代理与系统代理发现 |
| `timeout_seconds` | 240，范围 1-1800 秒 |
| `transport_retries` | 1，范围 0-3；仅 HTTP 408/425/429/500/502/503/504 有界重试 |
| `temperature` | 0.3；null 表示不发送该字段，适用于不接受该参数的模型 |
| `max_tokens` | 默认 `min(16000,max(2048,word_limit*5))`；可按服务上限指定正整数 |
| `max_tokens_field` | `max_tokens` 或 `max_completion_tokens`，默认前者 |
| `extra_body` | 可选 JSON 对象，用于供应商明确支持的参数；不得覆盖模型、messages、stream、temperature、token 上限或携带凭据 |

云端仅 HTTPS，HTTP 仅回环地址。base_url 不含用户名、密码、query 或 fragment。拒绝重定向，以免认证发往意外目的地。密钥不放在 JSON、命令行、提示词、笔记或提交物中。外部模型会接收本次 prompt 中的证据与作者材料；尊重用户已经选择的服务和数据范围，切换服务先取得明确选择。

请求采用 `stream:false`。结果读取 `choices[0].message.content`，保留 finish_reason 与 usage；空 final content 或其他协议形状报错。内部 reasoning 不用于正文或展示。响应回显当前密钥时丢弃该响应，错误响应只保留状态摘要。

## 留档与恢复

每轮 `request.json` 保存实际适配器参数、模型发现结果及输入哈希；通用接口另保存不含认证头的 `provider-request.json`。`raw.json`、`body.txt`、manifest 和审查门槛沿用同一流程。正文及证据属于任务私有材料，放在公开仓库外。

401/403、请求字段错误、模型缺失直接报告并让用户调整；不会静默改模型、渠道或开启代理。连接/超时错误可能已有推理执行，不盲目自动重发 POST；检查服务状态与已有留档后再决定。默认至多两轮内容修正，与 transport retries 分开计数。已成功生成的正文从留档恢复。

更换模型或渠道属于 task 变化，旧正文的批准哈希失效，须用更新后的任务重新生成并审查。模型差异只影响生成过程，题录、证据、Word 精确计数、语义审查及原生引文验收门槛一致。

可单独运行 `python scripts/model_client.py <task.json或渠道示例.json>` 探测模型列表；不提交写作请求。通用接口 secret 在当前进程环境解析，环境变量发生变化后须确认执行进程能读取它。
