# 双 DGX Spark + DeepSeek-V4-Flash-0731 完整部署迁移手册

> 版本：v1.0
> 时间：2026-08-27 06:32 +08:00
> 适用：2×NVIDIA DGX Spark GB10 双机部署 DeepSeek-V4-Flash-0731，从 NIM 迁移至 vLLM，接入 LiteLLM 网关

---

## 目录
1. [最终架构](#1-最终架构)
2. [环境基线](#2-环境基线)
3. [总体流程](#3-总体流程)
4. [第一阶段：NIM 双机部署](#4-第一阶段nim-双机部署)
5. [第二阶段：优化与基准](#5-第二阶段优化与基准)
6. [第三阶段：NIM → vLLM 迁移](#6-第三阶段nim--vllm-迁移)
7. [第四阶段：128K 上下文扩展](#7-第四阶段128k-上下文扩展)
8. [第五阶段：LiteLLM 网关](#8-第五阶段litellm-网关)
9. [核心经验与踩坑总结](#9-核心经验与踩坑总结)
10. [日常运维手册](#10-日常运维手册)
11. [后期优化方向](#11-后期优化方向)
12. [产物清单](#12-产物清单)

---

## 1. 最终架构

```
客户端 (opencode / Cursor / Open WebUI / 脚本)
        │
        ├── HTTP   http://172.19.51.123:4000/v1   ← LiteLLM 网关 HTTP（推荐，opencode 用）
        ├── HTTPS  https://172.19.51.123:4443/v1  ← LiteLLM 网关 HTTPS（自签名证书）
        │
        └──────────── 转发 ↓ ────────────
                         │
                    vLLM :18090（推理后端，128K 上下文）
                         │
                    2×DGX Spark TP=2 / RoCE / CX-7
```

**服务矩阵**

| 服务 | 地址 | 认证 | 状态 |
|---|---|---|---|
| LiteLLM HTTP 网关 | `http://172.19.51.123:4000/v1` | `sk-dgx-local-2026` | active |
| LiteLLM HTTPS 网关 | `https://172.19.51.123:4443/v1` | `sk-dgx-local-2026` | active |
| vLLM 推理 | `http://172.19.51.123:18090/v1` | 无（内网） | Up |
| NIM（回滚保留） | `http://172.19.51.123:8000/v1` | 无 | 已停 |

## 2. 环境基线

| 项 | Node 0（Rank 0/API） | Node 1（Rank 1） |
|---|---|---|
| 主机名 | cube-fe5e | cube-0137 |
| 管理 IP | 172.19.51.123 | 172.19.49.159 |
| CX-7 直连 | 192.168.100.10/24 | 192.168.100.11/24 |
| 直连接口 | enp1s0f0np0（rocep1s0f0, GID 3） | 同左 |
| GPU | DGX Spark GB10 / SM121 | 同左 |
| UMA | 121 GiB | 119 GiB |
| 运维账号 | dgxdeploy（NOPASSWD sudo） | 同左 |
| 模型缓存 | /opt/deepseek-v4/nim-cache | 同左 |
| 模型目录 | /data/models/DeepSeek-V4-Flash-0731（156G） | 同左 |

## 3. 总体流程

```
Phase A  NIM 部署与验收（CX-7/RDMA/NCCL/API）
Phase B  优化（warmup/prefix cache/推理模式）
Phase C  NIM → vLLM 迁移（40K crash 解决 + 64K/128K）
Phase D  LiteLLM 网关（key/别名/HTTPS）
```

## 4. 第一阶段：NIM 双机部署

### 4.1 前置
- SSH 免密：`dgxdeploy` 用户 + ed25519 密钥（NOPASSWD sudo）
- 网络验收：CX-7 200Gb/s 直连、RoCE GID 配置

### 4.2 直连网络（netplan）
```yaml
# /etc/netplan/40-cx7-deepseek.yaml（双机，先备份原配置）
network:
  version: 2
  ethernets:
    enp1s0f0np0:
      addresses: [192.168.100.10/24]   # Node1 用 .11
      dhcp4: false
```
> 顺序：临时 `ip addr add` 验证 → 再写 netplan → `netplan generate && apply`

### 4.3 验收关键指标
- RDMA：`ib_write_bw` ≈ **13 GB/s**
- NCCL：all_gather 16GB **busbw 22.2 GB/s**（RoCE，非 TCP fallback）
- API：`/v1/models` + chat 返回 `DGX_CLUSTER_OK`

### 4.4 NIM 关键参数（踩坑）
- 容器环境：`NCCL_SOCKET_IFNAME/GLOO_SOCKET_IFNAME=enp1s0f0np0`
- 缓存权限：容器 uid 1000(nvs)，目录须 `chown 1000:1000`
- manifest 重试：`NIM_MANIFEST_DOWNLOAD_MAX_RETRY_COUNT=300`（默认 0，代理抖就崩）

## 5. 第二阶段：优化与基准

### 5.1 性能对比（NIM warm baseline）
| Case | TTFT | Decode | TPOT |
|---|---|---|---|
| 1K | 0.26s | 19.6 tps | 51ms |
| 8K | 0.40s | 21.9 tps | 46ms |
| C2 并发 | — | 27.7 tps | — |

### 5.2 Prefix Cache 收益（Radix cache）
- 24K 共享前缀：TTFT **18.1s → 0.39s（改善 97.8%）**
- 启示：编程 prompt 保持稳定前缀顺序（system→规则→项目→历史→任务）

### 5.3 推理模式（chat_template_kwargs）
- Fast（默认）：`{"thinking":false}`
- Think：`{"thinking":true}`
- Max：`{"thinking":true,"reasoning_effort":"max"}`

## 6. 第三阶段：NIM → vLLM 迁移

### 6.1 迁移动因
- NIM 1.11.0-variant 存在 P0：**>32K prompt 触发 `sparse_mla_sm120_prefill.cu` CUDA crash**

### 6.2 关键决策
| 决策点 | 结论 |
|---|---|
| vLLM 版本 | 固定 digest（`ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731@sha256:676a1c89...`） |
| 模型 | revision `9e165c30...`（复用 NIM 缓存，sha256 校验一致，省 177GB 下载） |
| 配方 | liquidgravityai recipe，commit `8a161c09` |
| 配置 | MAX_MODEL_LEN=65536（canary64）→ 131072（canary128）|

### 6.3 迁移验收（核心）
| 项 | 结果 |
|---|---|
| 分布式 | world_size=2 / TP=2 / CX-7 RoCE |
| 40K/64K/128K | 全 3/3（40K crash 解决）|
| Prefix Cache | 64K 前缀 16.7s→0.7s |
| Tool calling | tool_choice=auto 正常 |
| 吞吐 | decode 47.3tps（2.4x NIM）|
| Soak | 128K 混合负载 PASS |

## 7. 第四阶段：128K 上下文扩展

### 7.1 配置（canary128）
```env
MAX_MODEL_LEN=131072
MAX_NUM_SEQS=1        # 降低并发换长上下文
KV_CACHE_MEMORY_BYTES=20000000000
GPU_MEMORY_UTILIZATION=0.86
ENABLE_PREFIX_CACHING=1
DEEPSEEK_THINKING=false
```
> 内存余量偏紧（soak 中 ~2.9GiB），若告警切回 canary64（MAX_NUM_SEQS=2）

## 8. 第五阶段：LiteLLM 网关

### 8.1 配置（/home/dgxdeploy/litellm_config.yaml）
- 模型别名：deepseek-local / deepseek-coding / deepseek-office（均指向 vLLM）
- master_key：sk-dgx-local-2026
- 隐私：disable_spend_logs + store_model_in_db:false
- request_timeout: 1800（长上下文）

### 8.2 双栈（systemd）
- `litellm.service`：HTTP :4000（opencode 用）
- `litellm-https.service`：HTTPS :4443（自签名证书，`--ssl_certfile_path`/`--ssl_keyfile_path`）

## 9. 核心经验与踩坑总结

### 9.1 网络/下载（最大坑）
1. **NGC 中国区封锁（HTTP 451）**：manifest 必须走非中国代理；权重 `xfiles.ngc.nvidia.com` 直连（无封锁），加入 NO_PROXY
2. **ghcr 下载**：
   - per-connection 限速（单连接 2-6MB/s），>16 并发触发代理崩溃
   - `curl -C -` 续传在 ghcr CDN 卡死（chunk 重试数百次不动）
   - 最终方案：**串行整层下载**（curl 内建续传单文件）最稳，18MB/s
   - 国内镜像源（daocloud/1ms）无此镜像
3. **本机 Python/curl 访问 DGX 走系统代理绕路**（11s 延迟），用 `--noproxy` 或 `ProxyHandler({})` 直连（0.3s）；opencode 读环境变量（无 HTTP_PROXY）→ 直连不受影响

### 9.2 Docker/部署
4. **docker load 不认 OCI layout**：需转 docker-archive（gzip 层解压）+ `docker load`；或 `ctr -n moby images import`（但 overlay2 存储下 docker 不可见）
5. **compose restart 不重读 env**：改 .env 后必须 down+up
6. **NIM/vLLM 不能共存**：UMA 121GB，双模型常驻 OOM；NIM 须 `docker update --restart=no` 防意外拉起
7. NIM 容器 `--restart=unless-stopped` 会在 daemon 重启后自动拉起（曾与 vLLM 争内存）→ 迁移期改 `--restart=no`

### 9.3 软件安装
8. LiteLLM 镜像源不可用 → **venv + pip 安装**（清华源），绕开镜像困境
9. PEP 668 限制 → 用 venv 而非 pip --user

### 9.4 证书/客户端
10. **opencode 1.18.23（Bun 运行时）不信任自签名证书**：导入 Windows 证书库、NODE_EXTRA_CA_CERTS 均无效
    → 网关做 **HTTP+HTTPS 双栈**，opencode 走 HTTP
11. 自签名证书必须含 `basicConstraints=CA:TRUE` + IP SAN（Bun/BoringSSL 严格）

### 9.5 其他
12. 缓存权限：容器 uid 1000
13. 8000 端口：Portainer 曾占用（edge agent），重建去掉 8000 映射
14. Clash/代理失效：`pkill verge-mihomo` 自动重启恢复
15. Node 0 曾整机无响应一次（113Gi 内存占用时），物理介入恢复——注意内存余量

## 10. 日常运维手册

```bash
# ===== 健康检查 =====
curl http://172.19.51.123:4000/v1/models -H 'Authorization: Bearer sk-dgx-local-2026'   # 网关 HTTP
curl -k https://172.19.51.123:4443/v1/models -H 'Authorization: Bearer sk-dgx-local-2026' # 网关 HTTPS
curl http://172.19.51.123:18090/health                                                     # vLLM
bash /opt/deepseek-v4/ops/scripts/status.sh                                               # 集群状态

# ===== 服务管理 =====
sudo systemctl restart litellm          # HTTP 网关
sudo systemctl restart litellm-https    # HTTPS 网关
cd ~/deepseek-v4-vllm/upstream
docker compose --env-file .env.canary128 down && docker compose --env-file .env.canary128 up -d   # 重启 vLLM

# ===== 回滚 NIM =====
docker start deepseek-v4-rank0   # Node 0，等 :8000 就绪
docker start deepseek-v4-rank1   # Node 1

# ===== 基准测试 =====
python3 /opt/deepseek-v4/ops/benchmark/bench_openai.py --cases A_1K_256,B_8K_512 --runs 5
```

## 11. 后期优化方向

- [ ] **2h/8h 长 soak**（当前仅 30min 验证）
- [ ] **256K 评估**：受 UMA 限制（权重 77.5Gi + KV 80Gi ≈ 157Gi > 121Gi），需 KV 量化或架构改进
- [ ] **Open WebUI** 办公入口（控制机，用 deepseek-office 别名 + RAG）
- [ ] **18090 内部化**：防火墙限制仅内网
- [ ] **真实证书**：有域名后替换自签名，opencode 可切 HTTPS
- [ ] **NIM 清理**：稳定运行后删除镜像/缓存释放磁盘
- [ ] **内存监控**：Node 0 曾整机无响应，建议监控 available 内存阈值

## 12. 产物清单

| 位置 | 内容 |
|---|---|
| `mydocs\deepseek-v4-flash-dual-dgx\` | 全部脚本/报告/手册/交接 |
| Node 0 `/home/dgxdeploy/litellm_config.yaml` | LiteLLM 配置 |
| Node 0 `/home/dgxdeploy/litellm-venv/` | LiteLLM 环境 |
| 双机 `~/deepseek-v4-vllm/` | rollback + upstream + .env.canary64/128 |
| 双机 `/data/models/DeepSeek-V4-Flash-0731/` | 模型 156G |
| Node 0 `/opt/deepseek-v4/ops/` | 快照 + benchmark + 运维脚本 |
| Node 0 `/data/vllm-oci*` | vLLM 镜像 OCI 中转（可清理） |
