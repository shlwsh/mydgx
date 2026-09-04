# Node0 迁移工作站 — 切换方案与执行清单

> 版本：v1.2
> 时间：2026-09-04
> 状态：**执行中 · 集群已上线（TP=2 验收通过），B10 soak 进行中**
> 相关手册：`../deepseek-v4-flash-dual-dgx/系统部署结果与运维手册_v1.0_20260827-0645.md`
> 相关报告：`../deepseek-v4-flash-dual-dgx/reports/incident-20260902-vllm-roce-gid.md`
>
> **v1.2 变更（执行实录）**：
> - **迁移语义确认**：Node1（cube-0137）持完整模型副本（156GB/48 shards）与同 ID vLLM 镜像（`74880bc55fa7`）→ 弃用 Node0 WiFi 慢传，改为**物理移线后 Node1 经 200G 光纤直传工作站**（B5，实测 SSH TCP ~170MB/s，增量续传达 ~613MB/s）。
> - **GID index 适配**：移线后两端主机 gid 表重排，IPv4 v2 GID 落于 **index 3**（原配方 index 4 失效）→ Node1 + 工作站 `.env.canary128` 均改 `IB_GID_INDEX=3`。RDMA 实测 `ib_write_bw`（gid3, RC 连接）通过。
> - **端口适配**：Node0 两根 CX7 线插入工作站**卡1.1/卡2.1 口**（`enp1s0f1np1`/`enP2p1s0f1np1`，rocep1s0f1/roceP2p1s0f1）→ 工作站 `FABRIC_IFACE=enp1s0f1np1`、`IB_HCA=rocep1s0f1`（非原配方的 enp1s0f0np0/rocep1s0f0）。
> - **静态互联核实**：两端 fabric 均 netplan `40-cx7-deepseek.yaml` 静态 `192.168.100.10/.11/24`、`dhcp4:false`、NM `manual`+`autoconnect=yes`、renderer NetworkManager——重启自动恢复。

---

## 1. 背景与目标

原 2×DGX Spark / GB10 双机 TP=2 集群：

| 角色 | 源 Node0（退役, 172.19.51.123→工作站接管） | Node1（不动） |
|---|---|---|
| 主机 | cube-fe5e → **cube-f22b（新工作站）** | cube-0137 |
| 管理 IP | ~~172.19.51.123~~ → **172.19.9.104（工作站）** | 172.19.49.159 |
| CX-7 直连 | 192.168.100.10（Rank0） | 192.168.100.11（Rank1） |
| 服务 | vLLM Rank0 + LiteLLM 网关 | vLLM Rank1 |

**本次目标**：用新工作站 `cube-f22b`（172.19.9.104）**替换 Node0**，保留 Node1 不变；节点少服务期间停机，将原 Node0 的直连光纤改插到工作站，恢复 `2×DGX Spark TP=2 / RoCE / 128K` 双机模式；最后退役 Node0。

工作站与 Node0 硬件同型（GB10 / aarch64 / 4×CX7，`rocep1s0f0` + mlx5/RDMA 驱动已就绪，仅光纤未接）。可行性已验证成立。

---

## 2. 已拍板决策（2026-09-04）

| 决策项 | 结论 |
|---|---|
| 客户端入口地址 | 直接用工作站新地址 `172.19.9.104`（改 `.env` / `opencode.json` / `claude settings.json`） |
| LiteLLM HTTPS 证书 | 为新地址重新自签（CN=172.19.9.104，IP SAN 含 172.19.9.104/127.0.0.1/localhost） |
| 工作站提权 | winbot 免密 NOPASSWD sudo（与双机 dgxdeploy 同构） |
| **切换方式（v1.1）** | **方案 B 硬切**：停机 → 物理移线（拔 Node0 直连光纤插工作站）→ 重组 (工作站+Node1) TP=2 |
| **传输源（v1.1）** | **Node1 为源**：模型 156GB + vLLM 镜像 24.8GB 移线后经 200G 光纤直传工作站；**取消 Node0 WiFi rsync** |

**已核实（2026-09-04，切换前置条件）**
- Node1（cube-0137）：模型 `/data/models/DeepSeek-V4-Flash-0731` 完整（156GB / 48 shards + config + tokenizer + checksums.blake3）；vLLM 镜像 `ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731:pinned-676a1c89` = `74880bc55fa7`（与 Node0 同 ID）；CX7 `enp1s0f0np0`=192.168.100.11（200Gb/s，carrier=1）；磁盘 1.4T 空闲。
- 工作站（cube-f22b）：4×CX7 口全部 carrier=0（无光纤）；Qwen3.6 容器已停（restart=no），**可用内存 15GB→117GB**（满足 Rank0 加载）；daemon.json（nvidia runtime）已写、重启延后 Phase B；netplan 40-cx7 已布、apply 延后 Phase B。

---

## 3. 迁移产物清单

> v1.1：模型与镜像**改以 Node1 为传输源**（Node1 持完整副本）；其余文件（配方/LiteLLM/证书/systemd/netplan）已在阶段 A 由 Node0 → 工作站落盘。

| 项 | 内容 | 源 | 目标路径（工作站） |
|---|---|---|---|
| 模型权重 | DeepSeek-V4-Flash-0731（156GB，48 shards） | **Node1**（经 200G 光纤） | `/data/models/DeepSeek-V4-Flash-0731` |
| vLLM 镜像 | `ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731:pinned-676a1c89`（arm64, 24.8GB） | **Node1**（docker save\|load） | Docker 本地 |
| vLLM 配方 | liquidgravityai 2x-dgx-spark recipe（commit `8a161c09...`） | Node0（已复制 ✅） | `~/deepseek-v4-vllm/upstream` |
| Docker 配置 | daemon.json（nvidia runtime） | Node0（已写，重启延后 ✅） | `/etc/docker/daemon.json` |
| 缓存目录 | deepseek-v4-vllm cache（chown 1000:1000） | Node0（已建 ✅） | `/data/cache/deepseek-v4-vllm` |
| LiteLLM venv | python3.12 + litellm==1.98.0（+proxy extras，与 Node0 逐包一致） | Node0（已完成 ✅） | `~/litellm-venv` |
| LiteLLM 配置 | `litellm_config.yaml`（4 别名，master_key sk-dgx-local-2026） | Node0（已完成 ✅） | `~/litellm_config.yaml` |
| LiteLLM hooks | `litellm_hooks.py`（125K 硬顶 + max_tokens 钳制） | Node0（已完成 ✅） | `~/litellm_hooks.py` |
| TLS 证书 | 新自签 `litellm.crt` / `litellm.key`（CN=172.19.9.104） | 工作站自签（已完成 ✅） | `~/litellm.crt` / `~/litellm.key` |
| systemd | `litellm.service`（:4000）+ `litellm-https.service`（:4443） | Node0 模板（已装 ✅） | `/etc/systemd/system/` |
| CX7 netplan | `40-cx7-deepseek.yaml`（192.168.100.10/24） | Node0（已布，apply 延后 ✅） | `/etc/netplan/` |
| 主机级守护 | vllm-watchdog / vllm-port-lockdown（winbot 版，已装 ✅） | Node0 模板 | `/etc/systemd/system/` + `~/ops/` |
| dgxdeploy 密钥 | winbot→Node1 SSH（watchdog 用，链路已通 ✅） | Node0 | `~/.ssh/id_ed25519` |
| Tailscale | cube-f22b 加入 `shlwsh@` tailnet | — | —（待 A8/用户授权） |

---

## 4. 阶段 A · 非侵入预置（Node0 照常服务，故障窗口 = 0）

> 本阶段所有步骤**不触碰现网 service**。**v1.1：阶段 A 已完成（除 A3 改为阶段 B 从 Node1 走光纤、A8 Tailscale 待授权）。**

- [x] A1. 工作站开通：winbot 免密 sudo + 加入 docker 组（`groups` 含 docker；`sudo -n true` 通过）
- [x] A2. 建目录 `/data/models`、`/data/cache/deepseek-v4-vllm`（chown 1000:1000）；Qwen3.6 停用释放内存（可用 15→117GB）
- [~] A3. ~~传输模型（Node0 WiFi rsync）~~ **已取消（v1.1）**：Node1 持完整模型，改为阶段 B4 走 200G 光纤直传。Node0 后台 rsync（~56GB 时）**切机前需 kill**
  - 校验 `sha256sum model-00001-of-00048.safetensors == f3668ba4cccf1ca6a7eb84e888fb92c1cdc7204d472ba9db771e6fd3abf6b874`
- [x] A4. 配置 Docker：
  - [x] daemon.json（nvidia runtime，写入 `/etc/docker/daemon.json`，**重启延后 Phase B**）
  - [ ] `docker pull ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731:pinned-676a1c89` → **改为 B4 从 Node1 `docker save|load`（同 ID 74880bc55fa7）**
- [x] A5. 克隆 vLLM 配方并冻结：Node0→工作站 rsync（`upstream/`，commit `8a161c09f1269ee8ea18ba324b4682d0712f4e4a`）
- [x] A6. 写 `.env.canary128` / `.env.canary64`（工作站，Rank0；字节同 Node0）：
  ```env
  NODE_RANK=0
  MASTER_ADDR=192.168.100.10
  VLLM_HOST_IP=192.168.100.10
  FABRIC_IFACE=enp1s0f0np0
  IB_HCA=rocep1s0f0
  IB_GID_INDEX=4
  MODEL_HOST_PATH=/data/models/DeepSeek-V4-Flash-0731
  CACHE_HOST_PATH=/data/cache/deepseek-v4-vllm
  API_PORT=18090
  MASTER_PORT=29501
  SERVED_MODEL_NAME=deepseek-v4-flash-0731
  KV_CACHE_MEMORY_BYTES=20000000000
  GPU_MEMORY_UTILIZATION=0.86
  MAX_MODEL_LEN=131072
  MAX_NUM_SEQS=1
  DEEPSEEK_THINKING=false
  ```
- [x] A7. 装 LiteLLM 全套（venv py3.12 + litellm 1.98.0 + proxy extras、config、hooks、新证书、systemd 4000/4443 已装启用未启动）
- [ ] A8. Tailscale 加入 `shlwsh@` tailnet（**待用户授权**，不阻塞移线）

---

## 5. 阶段 B · 方案 B 硬切：停机 → 物理移线 → 200G 传输 → 上线（短时降级窗口）

> 切机窗口不可避免：TP=2 推理在移线期间中断，预计分钟级~小时级（含 156GB 光纤传输 + JIT 预热）。
> v1.1：阶段 A 已全预置；移线后 Node1 经 200G 光纤将模型 + 镜像直传工作站（不再走 WiFi）。

- [x] B1. 预检（停机前，一次性）：
  - [x] Node1 基线记录：`rocep1s0f0` ACTIVE/LINK_UP，GID idx2/3=192.168.100.11，roceP2p1s0f0 亦 LINK_UP（双线）
  - [x] Node1 持完整模型（156G/48 shards）+ 同 ID 镜像 `74880bc55fa7` → 传输源改 Node1
- [x] B2. 停 vLLM（Rank1 先、Rank0 后）+ 停 Node0 rsync：
  - [x] Node1 `compose down`（容器移除，UMA 释放 112Gi free）
  - [x] Node0 `compose down`（Rank0 停）
  - [x] Node0 kill WiFi rsync（126.55GB 处中止，REMAIN=0）；删 rsync_model.sh/copy_repo.sh
  - [x] Node0 LiteLLM 双网关停（inactive）；vllm-watchdog.timer 禁用 + `.vllm_maintenance` marker（Node1 无 watchdog）
- [x] B3. 物理移线（人工）：
  - [x] 拔 Node0 两根 CX7 线 → 插工作站卡1.1/卡2.1（`enp1s0f1np1`/`enP2p1s0f1np1`）——**非原计划 .0 口**，已适配
  - [x] 工作站 `rdma link show`：rocep1s0f1 + roceP2p1s0f1 → LINK_UP
- [x] B4. 工作站网络 + Docker：
  - [x] netplan `40-cx7-deepseek.yaml` → `enp1s0f1np1=192.168.100.10/24`（**适配实际有线口**），netplan apply
  - [x] 互 ping `192.168.100.10 ↔ .11` 通（~1ms）
  - [x] **GID 适配**：两端 gid 表重排，IPv4 v2 在 **index 3**（非 4）→ `.env.canary128` 双端 `IB_GID_INDEX=4→3`
  - [x] RDMA 实测：`ib_write_bw`（Node1 rocep1s0f0 gid3 server ↔ WS rocep1s0f1 gid3 client）RC 连接成功
  - [x] `systemctl restart docker` → nvidia runtime 生效
- [x] B5. 资产经 200G 光纤 Node1→工作站（工作站主动拉，dgxdeploy 密钥免密）：
  - [x] 镜像：`ssh Node1 docker save | docker load` → ID `74880bc55fa7` 三机一致
  - [x] 模型：`rsync`（--partial 续传已到 126G 的 WiFi 部分）增量补齐，全程 ~170MB/s（末段 613MB/s），74 文件与 Node1 一致
  - [x] 校验 `sha256sum model-00001 == f3668ba4...`（工作站=Node1=参考值，三重一致）
- [x] B6. 启动 vLLM（Rank1 先、Rank0 后）：
  - [x] Node1 `compose up -d`（`world_size=2 rank=1`，等 master）
  - [x] 工作站 `compose up -d`（`world_size=2 rank=0`，NCCL 2.30.4 PYNCCL，TP=2/EP）
  - [x] instanttensor 155G 加载 + `Application startup complete`；两容器 restarts=0
- [x] B7. TP=2 验收：
  - [x] `/v1/models` → `deepseek-v4-flash-0731`, `max_model_len: 131072`
  - [x] chat smoke → **`VLLM_DGX_OK`**（fingerprint `...-tp2-...`）
- [x] B8. 起 LiteLLM（工作站）：
  - [x] `litellm.service` :4000 + `litellm-https.service` :4443 均 active
  - [x] `/v1/models` 4 别名返回；chat via deepseek-local → **`LITELLM_DGX_OK`**
- [x] B9. 客户端切换 `172.19.9.104`：
  - [x] 仓库 `.env` → 172.19.9.104
  - [x] `opencode.json` dgx baseURL → `http://172.19.9.104:4000/v1`
  - [x] `.claude/settings.json` ANTHROPIC_BASE_URL → `http://172.19.9.104:4000`
  - [x] 端到端：Windows 本机 curl → `CLIENT_MIGRATED_OK`（`-tp2-`）
- [x] 主机级守护（阶段 A 补）：工作站 vllm-watchdog.timer + vllm-port-lockdown 已启用；iptables :18090 loopback-only 生效；watchdog 手动 rc=0 no-op
- [ ] B10. 稳定性 soak（后台探针 /home/winbot/logs/soak-*.log 每 5min 记录）：
  - [ ] 2h soak（128K，0 crash/OOM/NCCL failure/rank restart）
  - [ ] 8h soak（通过后进入退役）
- [ ] B11. 退役 Node0（soak 通过后）：
  - [ ] 验证工作站+Node1 全部正常、soak 通过
  - [ ] 停止 / 断电 Node0（**保留磁盘作回滚备份，不删除**）

---

## 6. 回滚预案

若阶段 B 任何步骤失败：

1. 停工作站/Node1 的 vLLM（`docker compose down`），释放 UMA
2. 物理移线还原回原 Node0 的 `enp1s0f0np0`（Node1 端不动）
3. Node0 netplan 已在位，直接起 Rank0；Node1 起 Rank1
4. 恢复 Node0 的 LiteLLM（未停/未删）
5. 客户端回切 `172.19.51.123`（原 Node0；若已退役则此路径不可用）

> 注：若失败点在 B5（Node1→工作站传输）之后、工作站模型/镜像未删，仍可重复 B5 续传（`rsync --partial` + `docker save|load` 可重入），不构成回滚触发条件。

回滚禁止：删除 vLLM model、删除 Node0 磁盘、改动 Node1 netplan/CX7/RDMA。

---

## 7. 风险与约束

| 风险 | 说明/对策 |
|---|---|
| 单机内存瓶颈 | GB10 128GB 装不下 156GB 模型；工作站**只承担 Rank0**，不做单机推理 |
| 切机窗口 | 移线中断 TP=2；阶段 A 全预置 + 200G 光纤传输（B5）以最小化 |
| RoCE GID 漂移 | 改前按 incident-20260902 核对 sysfs GID 表；用当前 index **4** |
| 无代理拉镜像 | **已规避**：镜像从 Node1 `docker save\|load` 走 200G，不依赖 ghcr/代理 |
| 模型传输 | **已规避**：Node1 持完整副本，走 200G 光纤；sha256 校验 model-00001 |
| 物理移线错误 | B3 前记录 Node1 GID/RDMA 现状；回滚脚本在案；Node0 磁盘保留 |
| 端口互斥 | 勿让其他服务占 18090/4000/4443 |
| 证书信任 | 新自签证书需客户端重新信任（HTTPS）；HTTP 先行 |
| 主机级守护 | vllm-watchdog / vllm-port-lockdown 已在工作站重建（winbot 版） |

---

## 8. 命令速查（日常）

```bash
# 健康检查（迁移后）
curl http://172.19.9.104:4000/v1/models -H 'Authorization: Bearer sk-dgx-local-2026'
curl -k https://172.19.9.104:4443/v1/models -H 'Authorization: Bearer sk-dgx-local-2026'
curl http://172.19.9.104:18090/health

# 阶段 B5 · Node1 → 工作站 200G 传输（在 Node1 上执行）
# 模型 156GB
rsync -ah --partial --info=progress2 -e "ssh -o StrictHostKeyChecking=no" \
  /data/models/DeepSeek-V4-Flash-0731/ \
  winbot@192.168.100.10:/data/models/DeepSeek-V4-Flash-0731/
# 镜像 24.8GB
docker save ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731:pinned-676a1c89 \
  | ssh winbot@192.168.100.10 'docker load'

# 服务管理（工作站）
sudo systemctl status/restart litellm litellm-https

# vLLM（工作站 = Rank0）
cd ~/deepseek-v4-vllm/upstream
docker compose --env-file .env.canary128 up -d / down

# 模型校验
sha256sum /data/models/DeepSeek-V4-Flash-0731/model-00001-of-00048.safetensors
# 期望 f3668ba4cccf1ca6a7eb84e888fb92c1cdc7204d472ba9db771e6fd3abf6b874
```
