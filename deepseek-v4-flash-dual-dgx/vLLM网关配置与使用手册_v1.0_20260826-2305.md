# DeepSeek-V4-Flash-0731 vLLM 网关配置与使用手册

> 版本：v1.1
> 时间：2026-08-26 23:20 +08:00
> 适用：2×DGX Spark + vLLM（128K）+ LiteLLM 网关（HTTP+HTTPS 双栈）

## 1. 系统架构

```
客户端 (opencode / Cursor / Open WebUI / 脚本)
        │
        ├── HTTP   http://172.19.9.104:4000/v1    ← LiteLLM 网关 HTTP（opencode 等，推荐）
        │             key 认证 + 模型别名
        ├── HTTPS  https://172.19.9.104:4443/v1   ← LiteLLM 网关 HTTPS（自签名证书）
        │             key 认证 + 模型别名
        │
        │             两者都转发到 ↓
        │                                      vLLM :18090（推理后端）
        │
        └── HTTP  http://172.19.9.104:18090/v1   ← vLLM 直连（内网调试用）
                                                  │
                                                  ▼
                                           2×DGX Spark TP=2/RoCE
```

## 2. 服务清单

| 服务 | 地址 | 认证 | 用途 |
|---|---|---|---|
| LiteLLM 网关（HTTP） | `http://172.19.9.104:4000/v1` | `sk-dgx-local-2026` | 客户端入口（推荐，无证书问题） |
| LiteLLM 网关（HTTPS） | `https://172.19.9.104:4443/v1` | `sk-dgx-local-2026` | 需加密场景（自签名证书） |
| vLLM 推理 | `http://172.19.9.104:18090/v1` | 无（内网） | 推理后端（内部/调试） |
| NIM（回滚） | `http://172.19.9.104:8000/v1` | 无 | 已停，保留回滚 |

## 3. LiteLLM 网关配置详解

### 3.1 配置文件

路径：`/home/dgxdeploy/litellm_config.yaml`

```yaml
model_list:
  - model_name: deepseek-local
    litellm_params:
      model: openai/deepseek-v4-flash-0731
      api_base: http://127.0.0.1:18090/v1
      api_key: none
  - model_name: deepseek-coding
    litellm_params:
      model: openai/deepseek-v4-flash-0731
      api_base: http://127.0.0.1:18090/v1
      api_key: none
  - model_name: deepseek-office
    litellm_params:
      model: openai/deepseek-v4-flash-0731
      api_base: http://127.0.0.1:18090/v1
      api_key: none

litellm_settings:
  drop_params: true
  request_timeout: 1800      # 128K 长上下文慢推理，30 分钟超时
  num_retries: 2
  telemetry: false

general_settings:
  master_key: sk-dgx-local-2026
  store_model_in_db: false
  disable_spend_logs: true   # 不记录 prompt/completion（隐私）
```

### 3.2 systemd 服务（双栈）

两个 LiteLLM 实例，共用同一 `litellm_config.yaml`：

**HTTP 网关**：`/etc/systemd/system/litellm.service`（:4000）

```ini
[Service]
User=dgxdeploy
ExecStart=/home/dgxdeploy/litellm-venv/bin/litellm \
  --config /home/dgxdeploy/litellm_config.yaml \
  --port 4000 --host 0.0.0.0
Restart=on-failure
```

**HTTPS 网关**：`/etc/systemd/system/litellm-https.service`（:4443）

```ini
[Service]
User=dgxdeploy
ExecStart=/home/dgxdeploy/litellm-venv/bin/litellm \
  --config /home/dgxdeploy/litellm_config.yaml \
  --port 4443 --host 0.0.0.0 \
  --ssl_certfile_path /home/dgxdeploy/litellm.crt \
  --ssl_keyfile_path /home/dgxdeploy/litellm.key
Restart=on-failure
```

### 3.3 模型别名（语义区分）

| 别名 | 用途 | 后端 |
|---|---|---|
| `deepseek-coding` | 编程（opencode/Cursor） | deepseek-v4-flash-0731 |
| `deepseek-office` | 办公/文档（Open WebUI） | deepseek-v4-flash-0731 |
| `deepseek-local` | 通用 | deepseek-v4-flash-0731 |

三个别名指向同一后端，仅语义区分，便于日志/限流/后续拆分。

### 3.4 HTTPS 证书（自签名）

- 证书 `/home/dgxdeploy/litellm.crt`、私钥 `/home/dgxdeploy/litellm.key`
- 生成方式（含 IP SAN + CA 约束，Bun/BoringSSL 兼容）：

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout litellm.key -out litellm.crt -days 3650 \
  -subj "/CN=172.19.9.104/O=DGX-Local" \
  -addext "subjectAltName=IP:172.19.9.104,IP:127.0.0.1,DNS:localhost" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign" \
  -addext "extendedKeyUsage=serverAuth"
```

## 4. 直接访问 vLLM 模型的方法

vLLM 暴露 OpenAI 兼容 API（无认证），`http://172.19.9.104:18090/v1`：

### 4.1 列模型
```bash
curl http://172.19.9.104:18090/v1/models
```

### 4.2 健康检查
```bash
curl http://172.19.9.104:18090/health
```

### 4.3 推理（chat completions）
```bash
curl http://172.19.9.104:18090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash-0731","messages":[{"role":"user","content":"你好"}],"max_tokens":256,"temperature":0}'
```

### 4.4 推理模式控制（chat_template_kwargs）
```bash
# Fast（默认，无推理）
-d '{"model":"deepseek-v4-flash-0731","messages":[...],"chat_template_kwargs":{"thinking":false}}'

# Think（产生推理过程）
-d '{"model":"deepseek-v4-flash-0731","messages":[...],"chat_template_kwargs":{"thinking":true}}'

# Max（最大推理努力）
-d '{"model":"deepseek-v4-flash-0731","messages":[...],"chat_template_kwargs":{"thinking":true},"reasoning_effort":"max"}'
```

### 4.5 Tool calling（须 tool_choice=auto）
```bash
curl http://172.19.9.104:18090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash-0731","messages":[{"role":"user","content":"当前时间?"}],
       "tools":[{"type":"function","function":{"name":"get_time","description":"获取时间","parameters":{"type":"object","properties":{}}}}],
       "tool_choice":"auto","max_tokens":256}'
```

## 5. 客户端接入

### 5.1 curl（经网关 HTTP，需 key）
```bash
curl http://172.19.9.104:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -d '{"model":"deepseek-coding","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

### 5.1b curl（经网关 HTTPS，需 key + 跳过自签名验证）
```bash
curl -k https://172.19.9.104:4443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-dgx-local-2026' \
  -d '{"model":"deepseek-coding","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

### 5.2 Python（经网关 HTTP，OpenAI SDK）
```python
from openai import OpenAI
client = OpenAI(base_url="http://172.19.9.104:4000/v1",
                api_key="sk-dgx-local-2026")
resp = client.chat.completions.create(
    model="deepseek-coding",
    messages=[{"role":"user","content":"你好"}],
    max_tokens=256,
)
print(resp.choices[0].message.content)
```

### 5.3 Python（直连 vLLM）
```python
from openai import OpenAI
client = OpenAI(base_url="http://172.19.9.104:18090/v1", api_key="none")
resp = client.chat.completions.create(
    model="deepseek-v4-flash-0731",
    messages=[{"role":"user","content":"你好"}],
    max_tokens=256,
)
```

### 5.4 opencode（本机）

配置 `C:\Users\Administrator\.config\opencode\opencode.json`，provider `dgx`：
- 默认模型 `dgx/deepseek-coding`（另有 deepseek-local/deepseek-office）
- context 131072、output 8192

## 6. HTTPS 证书信任（已通过双栈解决）

### 现状（v1.1 起）
- 网关采用 **HTTP+HTTPS 双栈**：HTTP :4000 + HTTPS :4443
- **opencode 走 HTTP :4000**（实测正常），彻底规避自签名证书验证问题
- HTTPS :4443 供需要加密的场景使用（curl 加 `-k`，Python 用 `verify=False`）

### 背景说明（为何需要双栈）
- opencode 1.18.23（Bun 运行时）**无法信任自签名证书**：
  - 导入 Windows 系统证书库（CurrentUser\Root + LocalMachine\Root）仍报 `unable to verify the first certificate`
  - `NODE_EXTRA_CA_CERTS` / `SSL_CERT_FILE` 环境变量对该版本无效（Bun 的 BoringSSL 不读系统证书）
- 因此 HTTPS 单栈会导致 opencode 不可用，双栈是务实解法

### 后续可选优化
- **使用真实证书**：若有域名，用 Let's Encrypt/CF Origin 证书替换自签名（内网 IP 无法用公网 CA），之后 opencode 也可切回 HTTPS
- **升级 opencode**：检查新版是否修复 Bun 读系统证书/NODE_EXTRA_CA_CERTS

> 说明：本机 curl/Python 访问 HTTPS 网关需跳过验证（`-k` 或 `verify=False`），除非完成证书信任。

## 7. 运维命令

```bash
# 网关状态/重启（HTTP）
sudo systemctl status litellm
sudo systemctl restart litellm
# 网关状态/重启（HTTPS）
sudo systemctl status litellm-https
sudo systemctl restart litellm-https

# vLLM 状态（Node 0/1）
docker ps --filter name=deepseek-v4-flash
# 重启 vLLM（双机，先 rank1 后 rank0）
cd ~/deepseek-v4-vllm/upstream
docker compose --env-file .env.canary128 down && docker compose --env-file .env.canary128 up -d

# 证书更新后重启 HTTPS 网关
sudo systemctl restart litellm-https

# 回滚 NIM
docker start deepseek-v4-rank0   # Node 0，等 :8000
docker start deepseek-v4-rank1   # Node 1
```

## 8. 关键参数速查

| 参数 | 值 |
|---|---|
| vLLM 端口 | 18090 |
| 网关 HTTP 端口 | 4000 |
| 网关 HTTPS 端口 | 4443（自签名） |
| 网关 key | sk-dgx-local-2026 |
| vLLM 模型名 | deepseek-v4-flash-0731 |
| 网关别名 | deepseek-local / coding / office |
| 上下文 | 131072（128K） |
| 默认推理模式 | Fast（thinking=false） |
| 网关超时 | 1800s |
| 隐私 | 不记录 prompt/completion |
