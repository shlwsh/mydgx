# DeepSeek-V4-Flash-0731 双 DGX Spark 部署交接报告

> 交接日期：2026-08-26
> 部署状态：**在线运行中（PASS）**
> 服务地址：`http://172.19.51.123:8000/v1`（OpenAI-Compatible API）

---

## 1. 系统总览

```
                       管理网络 / API
                            │
                    172.19.51.123:8000
                            │
                  ┌─────────▼──────────┐
                  │ Node 0  cube-fe5e  │  Rank 0 / NIM API Server
                  │ DGX Spark GB10     │
                  │ 121 GiB 统一内存    │
                  └─────────┬──────────┘
                            │
                   ConnectX-7 / RoCE (200Gb/s)
                      enp1s0f0np0 直连
                    192.168.100.10 ↔ .11
                            │
                  ┌─────────▼──────────┐
                  │ Node 1  cube-0137  │  Rank 1 / Worker
                  │ DGX Spark GB10     │
                  │ 119 GiB 统一内存    │
                  └────────────────────┘
```

| 角色 | 主机名 | 管理 IP | 直连 IP | SSH 用户 |
|---|---|---|---|---|
| Node 0 / Rank 0 / API | cube-fe5e | 172.19.51.123 | 192.168.100.10 | dgxdeploy |
| Node 1 / Rank 1 | cube-0137 | 172.19.49.159 | 192.168.100.11 | dgxdeploy |

## 2. 访问凭证

| 项 | 说明 |
|---|---|
| Node 0 管理口 SSH | `bot@172.19.51.123`（部署初始用户，密码另存） |
| Node 1 管理口 SSH | `his_test@172.19.49.159`（部署初始用户，密码另存） |
| 运维统一账号 | `dgxdeploy@<管理IP>`，SSH 密钥：`~/.ssh/dgx_deepseek_v4_ed25519`（本机） |
| sudo | dgxdeploy 已配置 NOPASSWD sudo |
| NGC API Key | 用于 nvcr.io 登录/模型 manifest，另存于操作者处 |
| API 认证 | 服务端 `api_key` 可任意填写（NIM 未强制校验） |

## 3. 运行中的服务

| 节点 | 容器 | 用途 |
|---|---|---|
| Node 0 | `deepseek-v4-rank0` | Rank 0 + SGLang API Server（`--network=host`，8000 端口） |
| Node 1 | `deepseek-v4-rank1` | Rank 1 Worker（join `192.168.100.10:20000`） |

镜像：`nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant`（24.3GB）
模型权重缓存：`/opt/deepseek-v4/nim-cache/`（约 190GB/节点，48 个 safetensors 分片，fp8）

### 关键环境变量（容器内）
- `NCCL_SOCKET_IFNAME / GLOO_SOCKET_IFNAME = enp1s0f0np0`（CX-7 直连口）
- `HTTP_PROXY / HTTPS_PROXY`：
  - Node 0：`http://127.0.0.1:7897`（本机 Clash，2026-08-26 已切换，不再依赖隧道）
  - Node 1：`http://127.0.0.1:7890`（本机 FlClash）
- `NO_PROXY` 包含 `xfiles.ngc.nvidia.com`（权重直连，避开 NGC 中国区封锁）
- `NIM_MANIFEST_DOWNLOAD_MAX_RETRY_COUNT=300`（manifest 抓取重试）
- `NIM_PRIMARY_NODE=192.168.100.10` / `NIM_NODE_MANAGER_PORT=20000`（仅 Rank 1）

## 4. 网络配置

### 4.1 CX-7 直连（本部署新增，已持久化）
- 配置文件：`/etc/netplan/40-cx7-deepseek.yaml`（双机）
  - Node 0：`enp1s0f0np0 = 192.168.100.10/24`
  - Node 1：`enp1s0f0np0 = 192.168.100.11/24`
- 原 netplan 备份：`/var/backups/deepseek-v4/netplan-before-20260825-1903*/`
- 管理网（Wi-Fi wlP9s9）未改动。

### 4.2 网络验收结果
- RDMA（RoCE v2）：ib_write_bw ≈ **13 GB/s**
- NCCL：all_gather 16GB，busbw **22.2 GB/s**（走 RoCE，bootstrap 走管理网）
- NCCL 源码：`/home/dgxdeploy/nccl`（v2.30.7-1）+ `/home/dgxdeploy/nccl-tests`

### 4.3 SSH 隧道（Node 0，系统级服务）
| systemd unit | 监听 | 用途 |
|---|---|---|
| `proxy-tunnel2` | 127.0.0.1:7899 | CX-7 → Node1 FlClash 7890（**2026-08-26 已停用**，Node0 本机 Clash 7897 已恢复，Rank0 已切换，可删除） |
| `proxy-tunnel` | 127.0.0.1:7898 | 管理网路径（**已停用**，未使用） |

## 5. 运维手册

### 5.1 状态检查
```bash
# API 健康
curl -s http://172.19.51.123:8000/v1/models

# 双机容器
ssh -i ~/.ssh/dgx_deepseek_v4_ed25519 dgxdeploy@172.19.51.123 'docker ps | grep deepseek-v4'
ssh -i ~/.ssh/dgx_deepseek_v4_ed25519 dgxdeploy@172.19.49.159 'docker ps | grep deepseek-v4'

# 日志
ssh ... dgxdeploy@172.19.51.123 'docker logs -f deepseek-v4-rank0'
ssh ... dgxdeploy@172.19.49.159 'docker logs -f deepseek-v4-rank1'
```

### 5.2 重启
```bash
# 顺序：先 Rank 0，再 Rank 1
ssh ... dgxdeploy@172.19.51.123 'docker restart deepseek-v4-rank0'
ssh ... dgxdeploy@172.19.49.159 'docker restart deepseek-v4-rank1'
```
注意：Rank 0 启动后发布 `NIM_PRIMARY_NODE`，Rank 1 需在 10 分钟内 join；权重加载约需 5–10 分钟。

### 5.3 停止 / 回滚
已生成脚本（Node 0 `~/deepseek-v4-cluster/scripts/`，本机 `mydocs` 副本）：
```bash
bash ~/deepseek-v4-cluster/scripts/10-stop-cluster.sh      # 停容器
bash ~/deepseek-v4-cluster/scripts/11-rollback-network.sh  # 回滚 netplan + 停容器 + 停隧道
```
回滚只删除本部署创建的内容；镜像、模型缓存、原 netplan 备份均保留。

### 5.4 API 调用示例
```python
from openai import OpenAI
client = OpenAI(base_url="http://172.19.51.123:8000/v1", api_key="none")
resp = client.chat.completions.create(
    model="deepseek-ai/DeepSeek-V4-Flash-0731",
    messages=[{"role": "user", "content": "Hello"}],
)
print(resp.choices[0].message.content)
```

## 6. 关键排障经验（重要）

1. **NGC 中国区封锁（HTTP 451）**
   - `api.ngc.nvidia.com`（manifest/配置）必须经**非中国出口代理**，容器通过 `HTTPS_PROXY` 生效。
   - 权重文件 `xfiles.ngc.nvidia.com`（预签名 URL）**无地理封锁**，走直连，务必加入 `NO_PROXY` 以免经代理导致慢/中断。
   - 曾经的坑：`NO_PROXY` 里误含 `api.ngc.nvidia.com` 会让 manifest 直连被 451。

2. **manifest 抓取抖动 → 容器崩溃循环**
   - 解法：`NIM_MANIFEST_DOWNLOAD_MAX_RETRY_COUNT=300`、`NIM_MANIFEST_DOWNLOAD_WAIT_INTERVAL_MS=3000`。
   - 默认重试次数为 0，代理一抖就崩。

3. **缓存目录权限**
   - NIM 容器以 `nvs`(uid 1000) 运行，缓存目录须属 `1000:1000`，否则报 `Permission denied (os error 13)`。

4. **8000 端口冲突**
   - Node 0 原 Portainer 占用 8000（edge agent 端口）。已重建 Portainer 去掉 8000 映射（保留 9000/9443）。**若 Portainer 被重装，勿再映射 8000。**

5. **Node 0 Clash(7897)**：部署期间曾失效（残留 mihomo 进程状态损坏，kill 后由 clash-verge-service 重新拉起即恢复）。2026-08-26 已恢复，Rank 0 已切换回本机 7897，隧道已停用。若再失效，可重试：`sudo pkill verge-mihomo`（会自动重启）。
   - 曾依赖 Node 1 FlClash(7890)，现已解除。

6. **Docker 已改动**（系统级）
   - `nvidia-ctk runtime configure --runtime=docker`（nvidia runtime）
   - `/etc/systemd/system/docker.service.d/http-proxy.conf`（daemon 走本地代理 pull）

## 7. 性能基线（warmup 前，含首次 autotuning）

| 场景 | 端到端 | 吞吐 |
|---|---|---|
| 1K prompt / 256 out / c1 | 8.9 s | 14.4 tps |
| 8K prompt / 512 out / c1 | 24.4 s | 8.4 tps |
| 32K prompt / 1024 out / c1 | 20.8 s | 6.5 tps |
| 并发 2（2K/256） | — | 22.4 tps |
| 并发 4（2K/256） | — | 34.0 tps |

> 首次请求触发 SGLang autotuning，吞吐在 warmup 后提升；TTFT 由分布式 prefill 主导。

## 8. 已知限制 / 遗留事项

- [ ] 端口 8000 已由 NIM 使用；Portainer Web UI 在 9000/9443。
- [ ] 代理依赖 Node 0 本机 Clash(7897) 单点；Node 1 FlClash(7890) 已解除依赖，隧道已停用。
- [ ] benchmark 为基线值，未做 warmup 与调参（KV cache / chunked prefill / 长上下文 / MoE 通信优化）。
- [ ] 模型 cache 190GB×2 占磁盘；如需清理须经确认（`/opt/deepseek-v4`）。
- [ ] NGC API Key 有效期与 nvcr.io 登录状态需定期确认。

## 9. 产物清单

| 位置 | 内容 |
|---|---|
| 本机 `C:\Users\Administrator\mydocs\deepseek-v4-flash-dual-dgx\` | 全套脚本 + 报告 |
| Node 0 `~/deepseek-v4-cluster/` | 运维脚本 + deployment-report |
| `/opt/deepseek-v4/nim-cache` | 模型缓存（双机） |
| `/var/backups/deepseek-v4/` | 网络配置备份 |

## 10. 建议后续

1. warmup + 完整 benchmark（TTFT/decode tok/s 分离测量）。
2. 验证更长上下文（128K/1M）与并发压力。
3. 监控 Node 0 Clash(7897) 稳定性；失效时 `sudo pkill verge-mihomo` 即可自动恢复。
4. 接入 Cursor / OpenCode / LiteLLM / Open WebUI 等上层应用。
