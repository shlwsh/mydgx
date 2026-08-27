# 使用 LiteLLM 网关配置连接指南（附 opencode / Claude Code 示例）

> 版本：v1.1
> 时间：2026-08-27 07:20 +08:00
> 目标：让任意 OpenAI 兼容客户端（opencode / Claude Code / Python / Open WebUI）通过 LiteLLM 网关连接本地 DeepSeek-V4-Flash

---

## 1. LiteLLM 网关是什么

LiteLLM 是一个轻量 **OpenAI 兼容代理网关**，前端暴露标准 OpenAI API，后端转发到 vLLM 推理服务。

```
客户端（任意 OpenAI 兼容）
   │  baseURL + apiKey + model 别名
   ▼
LiteLLM 网关  :4000（HTTP）/ :4443（HTTPS）
   │
   ▼
vLLM  :18090
```

**作用**：
- 统一入口（客户端不直接碰推理后端）
- API key 认证（`sk-dgx-local-2026`）
- 模型别名（`deepseek-coding` / `deepseek-office` / `deepseek-local`）
- 后续可无感切换后端、限流、日志

## 2. 连接要素

| 参数 | 值 |
|---|---|
| Base URL | `http://172.19.51.123:4000/v1`（HTTP）<br>`https://172.19.51.123:4443/v1`（HTTPS，需跳过证书验证） |
| API Key | `sk-dgx-local-2026` |
| 模型名（别名） | `deepseek-coding` / `deepseek-office` / `deepseek-local` |
| 上下文 | 131072（128K） |
| 默认推理模式 | Fast（thinking=false） |

> ⚠️ 内网客户端推荐走 HTTP :4000（opencode 无法信任 HTTPS 自签名证书）；HTTPS :4443 需 `-k`/`verify=False`。

## 3. opencode 连接示例（核心）

### 3.1 配置文件

路径：`C:\Users\Administrator\.config\opencode\opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "dgx/deepseek-coding",
  "provider": {
    "dgx": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DGX DeepSeek V4 (LiteLLM 网关)",
      "options": {
        "baseURL": "http://172.19.51.123:4000/v1",
        "apiKey": "sk-dgx-local-2026",
        "timeout": false,
        "headerTimeout": 300000
      },
      "models": {
        "deepseek-coding": {
          "name": "DeepSeek V4 (coding)",
          "tool_call": true,
          "reasoning": true,
          "limit": { "context": 131072, "output": 8192 },
          "variants": {
            "fast": { "body": { "chat_template_kwargs": { "thinking": false } } },
            "think": { "body": { "chat_template_kwargs": { "thinking": true } } },
            "max":   { "body": { "chat_template_kwargs": { "thinking": true }, "reasoning_effort": "max" } }
          }
        },
        "deepseek-local": {
          "name": "DeepSeek V4 (local)",
          "tool_call": true, "reasoning": true,
          "limit": { "context": 131072, "output": 8192 }
        },
        "deepseek-office": {
          "name": "DeepSeek V4 (office)",
          "tool_call": true, "reasoning": true,
          "limit": { "context": 131072, "output": 8192 }
        }
      }
    }
  }
}
```

### 3.2 配置要点说明

| 配置项 | 说明 |
|---|---|
| `npm` | `@ai-sdk/openai-compatible`（让 opencode 用 OpenAI 兼容协议） |
| `baseURL` | 指向 LiteLLM 网关（不是 vLLM 18090，也不是模型直连） |
| `apiKey` | LiteLLM 的 master key |
| `model` | `dgx/deepseek-coding`（opencode 引用名 = provider 名 + models 键） |
| `limit.context` | 131072（对应 vLLM MAX_MODEL_LEN） |
| `variants` | fast/think/max 三种推理模式（通过 `chat_template_kwargs` 控制） |
| `timeout` / `headerTimeout` | 放宽（长上下文慢推理） |

### 3.3 使用方式

```bash
# 指定模型
opencode run -m dgx/deepseek-coding "帮我写个 Python 快排"

# 切换推理模式（variant）
# 交互内用 /variants 切换 fast / think / max
# 命令行：
opencode run -m dgx/deepseek-coding "解释这段代码"   # 默认 fast（thinking=false）
```

### 3.4 切换模型别名

```bash
# 编程用
opencode run -m dgx/deepseek-coding "..."  
# 通用/办公
opencode run -m dgx/deepseek-local "..."
opencode run -m dgx/deepseek-office "..."
```

### 3.5 验证（实测）
```bash
opencode run -m dgx/deepseek-coding "Reply with exactly one word: READY OK"
# → READY OK
```

## 4. Claude Code 接入示例

Claude Code 使用 **Anthropic Messages API 协议**（不是 OpenAI 协议），需通过 LiteLLM 的 Anthropic 兼容端点 `/v1/messages` 接入。

### 4.1 前置：网关添加 Anthropic 模型别名

Claude Code 只接受它认识的 Anthropic 模型名。需在 LiteLLM 配置中添加 `claude-sonnet-4-5` 别名（映射到实际模型）：

`/home/dgxdeploy/litellm_config.yaml`（Node 0）追加：
```yaml
model_list:
  - model_name: claude-sonnet-4-5
    litellm_params:
      model: openai/deepseek-v4-flash-0731
      api_base: http://127.0.0.1:18090/v1
      api_key: none
```
改后重启：`sudo systemctl restart litellm litellm-https`

### 4.2 配置 Claude Code（本机）

文件：`C:\Users\Administrator\.claude\settings.json`
```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-dgx-local-2026",
    "ANTHROPIC_BASE_URL": "http://172.19.51.123:4000"
  },
  "model": "claude-sonnet-4-5",
  "includeCoAuthoredBy": false
}
```

> ⚠️ **关键**：`ANTHROPIC_BASE_URL` 不能带 `/v1` 后缀（Claude Code 会自动追加 `/v1/messages`）。带 `/v1` 会导致请求 404。

### 4.3 使用方式
```bash
claude                        # 进入交互（默认模型 claude-sonnet-4-5）
claude "帮我写个 Python 快排"  # 直接提问
claude --print "..."          # 非交互单次输出
claude --continue             # 继续上次会话
```

### 4.4 验证（实测）
```bash
claude --print "Reply with exactly two words: DEFAULT OK"
# → DEFAULT OK
```
Tool calling（列目录/读文件/回答）实测通过。

### 4.5 其他客户端：Anthropic 协议直连（curl）
```bash
curl http://172.19.51.123:4000/v1/messages \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: sk-dgx-local-2026' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"你好"}]}'
```

## 5. 其他客户端连接示例（对比）

### 5.1 Python（OpenAI SDK）
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://172.19.51.123:4000/v1",
    api_key="sk-dgx-local-2026",
)

resp = client.chat.completions.create(
    model="deepseek-coding",          # 用 LiteLLM 别名
    messages=[{"role": "user", "content": "用中文介绍北京"}],
    max_tokens=256,
)
print(resp.choices[0].message.content)
```

### 5.2 curl
```bash
curl http://172.19.51.123:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -d '{"model":"deepseek-coding","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

### 5.3 LangChain
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://172.19.51.123:4000/v1",
    api_key="sk-dgx-local-2026",
    model="deepseek-office",
)
```

### 5.4 Open WebUI（浏览器）
Admin Settings → Connections → OpenAI API：
- API Base URL：`http://172.19.51.123:4000/v1`
- API Key：`sk-dgx-local-2026`
- 模型：`deepseek-office`

## 6. 连接自检清单

| 检查项 | 命令 |
|---|---|
| 网关可达 | `curl http://172.19.51.123:4000/v1/models -H 'Authorization: Bearer sk-dgx-local-2026'` |
| key 有效 | 上面返回模型别名即 OK；401 则 key 错 |
| 模型可用 | `curl :4000/v1/chat/completions ...`（见 §5.2） |
| Anthropic 端点 | `curl :4000/v1/messages ...`（见 §4.5） |
| 后端健康 | `curl http://172.19.51.123:18090/health` |

## 7. 常见问题

**Q1: 返回 401？**
→ 检查 apiKey 是否 `sk-dgx-local-2026`；LiteLLM 未设 key 时会拒绝。

**Q2: opencode 报 "unable to verify the first certificate"？**
→ opencode 用了 HTTPS :4443。改为 HTTP :4000（baseURL 去 https），或等 opencode 支持自定义 CA。

**Q3: Claude Code 报 "There's an issue with the selected model"？**
→ 用 Claude Code 认识的 Anthropic 模型名（`claude-sonnet-4-5`），并确保网关有对应别名；自定义名（如 deepseek-coding）会被本地拒绝。

**Q4: Claude Code 报 404 / 连不上？**
→ 检查 `ANTHROPIC_BASE_URL` 是否误带 `/v1` 后缀（应为 `http://172.19.51.123:4000`）；settings.json 的 env 会覆盖系统环境变量。

**Q5: 推理很慢/超时？**
→ 128K 长上下文慢属正常；网关 `request_timeout=1800s`；客户端也需放宽超时（opencode `headerTimeout: 300000`）。

**Q6: 想用 think/max 推理？**
→ opencode 用 variants；其他客户端在请求体加 `"chat_template_kwargs":{"thinking":true}`。

**Q7: 网关重启后连不上？**
→ `sudo systemctl status litellm`；重启 `sudo systemctl restart litellm litellm-https`。

## 8. 参考

- LiteLLM 文档：https://docs.litellm.ai/
- opencode 文档：https://opencode.ai/docs/providers/（OpenAI 兼容本地模型）
- Claude Code 配置：`C:\Users\Administrator\.claude\settings.json`
- 网关配置：`/home/dgxdeploy/litellm_config.yaml`（Node 0）
