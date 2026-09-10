# 使用 LiteLLM 网关配置连接指南（附 opencode / Claude Code 示例）

> 版本：v2.0（地址更新：2026-09-10 晚）
> 时间：2026-09-10 +08:00
> 上一版：v1.1（2026-08-27）
>
> ⚠️ **地址更新（2026-09-10 晚）**：工作站原管理地址 `172.19.9.104` 已失效（有线口断开），现为**无线静态地址 `172.19.50.70`**。
> 详见 `故障处理记录_20260910_网关地址变更与vLLM-GID修复.md`。本手册全文已更新为 `172.19.50.70`。
> 目标：让任意 OpenAI 兼容客户端（opencode / Claude Code / Python / Open WebUI）通过 LiteLLM 网关连接本地 DeepSeek-V4-Flash
>
> **v2.0 主要变更**：
> 1. 网关启用 **虚拟 key（Virtual Keys / 多密钥）**，新增测试验证专用 key；
> 2. 后端新增 **PostgreSQL**（虚拟 key 与用量日志的存储依赖）；
> 3. 新增 **用量日志 / 调用记录分析** 章节；
> 4. **校正配置路径**：Node0 已迁移至工作站 `cube-f22b`（用户 `winbot`），配置从 `/home/dgxdeploy/` 改为 `/home/winbot/`。

---

## 1. LiteLLM 网关是什么

LiteLLM 是一个轻量 **OpenAI 兼容代理网关**，前端暴露标准 OpenAI API，后端转发到 vLLM 推理服务。

```
客户端（任意 OpenAI 兼容）
   │  baseURL + apiKey + model 别名
   ▼
LiteLLM 网关  :4000（HTTP）/ :4443（HTTPS）
   │  ├─ 认证：master key / 虚拟 key（PostgreSQL）
   │  └─ 用量日志：LiteLLM_SpendLogs
   ▼
vLLM  :18090
```

**作用**：
- 统一入口（客户端不直接碰推理后端）
- API key 认证（主密钥 + 可扩展的虚拟 key）
- 模型别名（`deepseek-coding` / `deepseek-office` / `deepseek-local` / `claude-sonnet-4-5`）
- 多 key 并存、按 key 的用量统计与调用记录分析
- 后续可无感切换后端、限流、配额

## 2. 连接要素

| 参数 | 值 |
|---|---|
| Base URL | `http://172.19.50.70:4000/v1`（HTTP）<br>`https://172.19.50.70:4443/v1`（HTTPS，需跳过证书验证） |
| 主密钥（master_key） | `sk-dgx-local-2026`（正式客户端沿用） |
| 测试专用 key | `sk-jACQ6S5DS23Ttjpn6OXnmg`（alias `dgx-validation-test`，仅限测试） |
| 模型名（别名） | `deepseek-coding` / `deepseek-office` / `deepseek-local` / `claude-sonnet-4-5` |
| 上下文 | 131072（128K） |
| 默认推理模式 | Fast（thinking=false） |

> ⚠️ 内网客户端推荐走 HTTP :4000（opencode 无法信任 HTTPS 自签名证书）；HTTPS :4443 需 `-k`/`verify=False`。
>
> ⚠️ 测试 key 仅用于测试验证，不要用于正式客户端；正式客户端请用主密钥。

## 3. 认证与密钥体系（v2.0 新增）

### 3.1 两类 key

| 类型 | 说明 | 用途 |
|---|---|---|
| 主密钥 master_key | 配置于 `general_settings.master_key`，固定单一，拥有管理权限 | 正式客户端、管理 API |
| 虚拟 key Virtual Key | 通过管理 API 动态生成，可多个并存，可设模型范围/元数据/配额 | 按用途/团队/测试隔离 |

> 主密钥是**单值**，无法同时存在两个静态 key；需要"多个 key 并存"必须用虚拟 key。
> 虚拟 key 依赖 **PostgreSQL**（SQLite 不支持 key 管理）。

### 3.2 现有 key 一览

| alias | key | 范围 | 说明 |
|---|---|---|---|
| （master） | `sk-dgx-local-2026` | 全部 | 主密钥，正式客户端 |
| `dgx-validation-test` | `sk-jACQ6S5DS23Ttjpn6OXnmg` | deepseek-coding/local/office | 测试验证专用，可吊销 |

### 3.3 密钥管理 API（需主密钥）

```bash
# 生成新 key（可加 metadata 便于分析；注意 tags 为企业版功能，开源版勿用）
curl -X POST http://172.19.50.70:4000/key/generate \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -H 'Content-Type: application/json' \
  -d '{"key_alias":"my-key","models":["deepseek-coding"],"metadata":{"owner":"team-a"}}'

# 列出全部 key
curl http://172.19.50.70:4000/key/list -H 'Authorization: Bearer sk-dgx-local-2026'

# 查看某 key 详情与用量
curl 'http://172.19.50.70:4000/key/info?key=<KEY>' -H 'Authorization: Bearer sk-dgx-local-2026'

# 吊销 key
curl -X POST http://172.19.50.70:4000/key/delete \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -H 'Content-Type: application/json' \
  -d '{"keys":["<KEY>"]}'
```

### 3.4 服务端信息（运维）

| 项 | 值 |
|---|---|
| 主机 | Node0 / 工作站 `cube-f22b`，`172.19.50.70`，SSH `winbot@172.19.50.70` |
| 配置文件 | `/home/winbot/litellm_config.yaml` |
| 证书/私钥 | `/home/winbot/litellm.crt` / `/home/winbot/litellm.key` |
| 服务 | `litellm.service`（:4000）、`litellm-https.service`（:4443） |
| 数据库 | PostgreSQL 16，库 `litellm`，用户 `litellm_user`（本机 127.0.0.1:5432） |
| DB 注入 | `/etc/systemd/system/litellm.service.d/99-pg.conf`（`DATABASE_URL`） |
| vLLM 后端 | `http://127.0.0.1:18090/v1` |

```bash
# 重启网关
sudo systemctl restart litellm.service litellm-https.service
# 查看状态
sudo systemctl status litellm.service litellm-https.service postgresql
```

## 4. opencode 连接示例（核心）

### 4.1 配置文件

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
        "baseURL": "http://172.19.50.70:4000/v1",
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

### 4.2 配置要点说明

| 配置项 | 说明 |
|---|---|
| `npm` | `@ai-sdk/openai-compatible`（让 opencode 用 OpenAI 兼容协议） |
| `baseURL` | 指向 LiteLLM 网关（不是 vLLM 18090，也不是模型直连） |
| `apiKey` | LiteLLM 的主密钥（或分配给该客户端的虚拟 key） |
| `model` | `dgx/deepseek-coding`（opencode 引用名 = provider 名 + models 键） |
| `limit.context` | 131072（对应 vLLM MAX_MODEL_LEN） |
| `variants` | fast/think/max 三种推理模式（通过 `chat_template_kwargs` 控制） |
| `timeout` / `headerTimeout` | 放宽（长上下文慢推理） |

### 4.3 使用方式

```bash
# 指定模型
opencode run -m dgx/deepseek-coding "帮我写个 Python 快排"

# 切换推理模式（variant）
# 交互内用 /variants 切换 fast / think / max
# 命令行：
opencode run -m dgx/deepseek-coding "解释这段代码"   # 默认 fast（thinking=false）
```

### 4.4 切换模型别名

```bash
# 编程用
opencode run -m dgx/deepseek-coding "..."
# 通用/办公
opencode run -m dgx/deepseek-local "..."
opencode run -m dgx/deepseek-office "..."
```

### 4.5 验证（实测）
```bash
opencode run -m dgx/deepseek-coding "Reply with exactly one word: READY OK"
# → READY OK
```

## 5. Claude Code 接入示例

Claude Code 使用 **Anthropic Messages API 协议**（不是 OpenAI 协议），需通过 LiteLLM 的 Anthropic 兼容端点 `/v1/messages` 接入。

### 5.1 前置：网关 Anthropic 模型别名

Claude Code 只接受它认识的 Anthropic 模型名。网关已配置 `claude-sonnet-4-5` 别名（映射到实际模型）：

`/home/winbot/litellm_config.yaml`（Node0）：
```yaml
model_list:
  - model_name: claude-sonnet-4-5
    litellm_params:
      model: openai/deepseek-v4-flash-0731
      api_base: http://127.0.0.1:18090/v1
      api_key: none
```
改后重启：`sudo systemctl restart litellm.service litellm-https.service`

### 5.2 配置 Claude Code（本机）

文件：`C:\Users\Administrator\.claude\settings.json`
```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-dgx-local-2026",
    "ANTHROPIC_BASE_URL": "http://172.19.50.70:4000"
  },
  "model": "claude-sonnet-4-5",
  "includeCoAuthoredBy": false
}
```

> ⚠️ **关键**：`ANTHROPIC_BASE_URL` 不能带 `/v1` 后缀（Claude Code 会自动追加 `/v1/messages`）。带 `/v1` 会导致请求 404。

### 5.3 使用方式
```bash
claude                        # 进入交互（默认模型 claude-sonnet-4-5）
claude "帮我写个 Python 快排"  # 直接提问
claude --print "..."          # 非交互单次输出
claude --continue             # 继续上次会话
```

### 5.4 验证（实测）
```bash
claude --print "Reply with exactly two words: DEFAULT OK"
# → DEFAULT OK
```
Tool calling（列目录/读文件/回答）实测通过。

### 5.5 其他客户端：Anthropic 协议直连（curl）
```bash
curl http://172.19.50.70:4000/v1/messages \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: sk-dgx-local-2026' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"你好"}]}'
```

## 6. 其他客户端连接示例（对比）

### 6.1 Python（OpenAI SDK）
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://172.19.50.70:4000/v1",
    api_key="sk-dgx-local-2026",     # 也可换成分配的虚拟 key
)

resp = client.chat.completions.create(
    model="deepseek-coding",          # 用 LiteLLM 别名
    messages=[{"role": "user", "content": "用中文介绍北京"}],
    max_tokens=256,
)
print(resp.choices[0].message.content)
```

### 6.2 curl
```bash
curl http://172.19.50.70:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -d '{"model":"deepseek-coding","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

### 6.3 LangChain
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://172.19.50.70:4000/v1",
    api_key="sk-dgx-local-2026",
    model="deepseek-office",
)
```

### 6.4 Open WebUI（浏览器）
Admin Settings → Connections → OpenAI API：
- API Base URL：`http://172.19.50.70:4000/v1`
- API Key：`sk-dgx-local-2026`（或分配的虚拟 key）
- 模型：`deepseek-office`

## 7. 多 key 与调用记录分析（v2.0 新增）

### 7.1 设计思路

为便于区分用途与后期分析，按用途各分配一个**虚拟 key**，并填 `metadata`：

```bash
curl -X POST http://172.19.50.70:4000/key/generate \
  -H 'Authorization: Bearer sk-dgx-local-2026' -H 'Content-Type: application/json' \
  -d '{
        "key_alias": "team-a-coding",
        "models": ["deepseek-coding"],
        "metadata": {"owner": "team-a", "purpose": "coding"}
      }'
```

> 注意：`tags` 为 **LiteLLM 企业版**功能，开源版传该字段会返回 403；请用 `metadata` 代替。

### 7.2 用量日志

配置 `general_settings.disable_spend_logs: false`（已启用），每次调用写入
PostgreSQL 表 `LiteLLM_SpendLogs`，字段含 `api_key`（token 哈希）、`total_tokens`、
`request_duration_ms`、`model_group`、`startTime` 等。

```bash
# 通过 API 查看调用记录（按 key 过滤可用 key=<虚拟key>）
curl 'http://172.19.50.70:4000/spend/logs' -H 'Authorization: Bearer sk-dgx-local-2026'

# 直接查 PostgreSQL（按 key/时间分析）
PGPASSWORD=litellm_pg_2026 psql -h 127.0.0.1 -U litellm_user -d litellm -c \
  "SELECT \"startTime\", model_group, total_tokens, request_duration_ms, api_key
   FROM \"LiteLLM_SpendLogs\" ORDER BY \"startTime\" DESC LIMIT 20;"
```

> 日志按批次落库，通常有数秒延迟。日志随调用量增长，后续可按需归档。

## 8. 连接自检清单

| 检查项 | 命令 |
|---|---|
| 网关可达 | `curl http://172.19.50.70:4000/v1/models -H 'Authorization: Bearer sk-dgx-local-2026'` |
| key 有效 | 上面返回模型别名即 OK；401 则 key 错 |
| 模型可用 | `curl :4000/v1/chat/completions ...`（见 §6.2） |
| Anthropic 端点 | `curl :4000/v1/messages ...`（见 §5.5） |
| 虚拟 key 功能 | `curl :4000/key/list -H 'Authorization: Bearer sk-dgx-local-2026'` |
| 用量日志 | `curl :4000/spend/logs -H 'Authorization: Bearer sk-dgx-local-2026'` |
| 后端健康 | `curl http://172.19.50.70:18090/health` |

## 9. 常见问题

**Q1: 返回 401？**
→ 检查 apiKey 是否正确（主密钥 `sk-dgx-local-2026` 或已生成的虚拟 key）；LiteLLM 未设 key 或 key 已吊销时会拒绝。

**Q2: opencode 报 "unable to verify the first certificate"？**
→ opencode 用了 HTTPS :4443。改为 HTTP :4000（baseURL 去 https），或等 opencode 支持自定义 CA。

**Q3: Claude Code 报 "There's an issue with the selected model"？**
→ 用 Claude Code 认识的 Anthropic 模型名（`claude-sonnet-4-5`），并确保网关有对应别名；自定义名（如 deepseek-coding）会被本地拒绝。

**Q4: Claude Code 报 404 / 连不上？**
→ 检查 `ANTHROPIC_BASE_URL` 是否误带 `/v1` 后缀（应为 `http://172.19.50.70:4000`）；settings.json 的 env 会覆盖系统环境变量。

**Q5: 推理很慢/超时？**
→ 128K 长上下文慢属正常；网关 `request_timeout=1800s`；客户端也需放宽超时（opencode `headerTimeout: 300000`）。

**Q6: 想用 think/max 推理？**
→ opencode 用 variants；其他客户端在请求体加 `"chat_template_kwargs":{"thinking":true}`。

**Q7: 网关重启后连不上？**
→ `sudo systemctl status litellm.service`；重启 `sudo systemctl restart litellm.service litellm-https.service`。

**Q8: `/key/generate` 返回 500 "DB not connected"？**
→ 虚拟 key 依赖 PostgreSQL。检查 `postgresql` 服务、`DATABASE_URL`（systemd drop-in）与 Prisma Client；详见
`docs/LiteLLM虚拟密钥启用与测试key记录_20260910.md`。

**Q9: `/key/generate` 返回 403 "Enterprise users: tags"？**
→ 请求体带了 `tags` 字段（企业版专有）。去掉 `tags`，改用 `metadata`。

**Q10: 服务反复重启、网关不可用？**
→ 多为 DB/Prisma 初始化异常。查看 `sudo journalctl -u litellm.service -n 100`；确认已 `prisma generate` 且 `prisma migrate deploy` 完成。

## 10. 参考

- LiteLLM 文档：https://docs.litellm.ai/
- LiteLLM 虚拟 key：https://docs.litellm.ai/docs/proxy/virtual_keys
- opencode 文档：https://opencode.ai/docs/providers/（OpenAI 兼容本地模型）
- Claude Code 配置：`C:\Users\Administrator\.claude\settings.json`
- 网关配置：`/home/winbot/litellm_config.yaml`（Node0 / 工作站 cube-f22b）
- 虚拟密钥启用记录：`docs/LiteLLM虚拟密钥启用与测试key记录_20260910.md`
