# 事故记录：vLLM 双机推理不可用（RoCE GID 漂移导致 NCCL 建链失败）

> 日期：2026-09-02
> 影响：LiteLLM 网关（:4000/:4443）500、vLLM（:18090）完全不可用
> 状态：**已修复并验证通过**
> 修复人：运维排查（本仓库 2026-09-02 会话）

---

## 1. 现象

- `curl http://172.19.9.104:18090/health` 无响应（后端未监听）。
- LiteLLM 进程/systemd 正常（4000/4443 在听），`/v1/models` 返回 200，但 `/v1/chat/completions` 报
  `litellm.exceptions.InternalServerError: Connection error`（后端不可达）。
- Node 0 容器 `deepseek-v4-flash-0731-0` 已于当日 01:57 退出（exit 0）；Node 1 容器
  `deepseek-v4-flash-0731-1` 仍 "Up" 但 worker 悬挂（rank0 已死，NCCL 收不到对端）。

## 2. 根因

vLLM 启动时 NCCL 经 RoCE（`rocep1s0f0` / CX-7 直连）建链失败：

```
NCCL WARN Call to ibv_modify_qp failed with 61 No data available, on dev rocep1s0f0:1,
        curr state INIT, next state RTR, local GID index 3, local GID ::, remote GID ::
RuntimeError: NCCL error: unhandled system error
```

排查链路状态（均正常，排除物理链路故障）：

| 检查项 | Node 0 | Node 1 |
|---|---|---|
| 网卡 | `enp1s0f0np0` UP，200Gb/s，Link detected yes | 同左 |
| RDMA | `rocep1s0f0/1 state ACTIVE, LINK_UP` | 同左 |
| 直连 ping | `192.168.100.11` 通（<1ms） | `192.168.100.10` 通 |

真正问题在 **GID 表布局漂移**。两机 RoCEv2 IPv4 GID 现在位于 **index 4**，
而 index 3 为空（旧驱动/配置布局中 index 3 是 RoCEv2 IPv4 GID，因此原配置用 3 可跑 6 天）：

```
# /sys/class/infiniband/rocep1s0f0/ports/1/gids/  （两机一致）
index 0  IB/RoCE v1  fe80::722a:d7ff:feXX:XXXX
index 1  RoCE v2     fe80::722a:d7ff:feXX:XXXX
index 2  IB/RoCE v1  ::ffff:c0a8:640a  (Node1: 640b)   ← IPv4 GID (RoCEv1)
index 3  （空）
index 4  RoCE v2     ::ffff:c0a8:640a  (Node1: 640b)   ← IPv4 GID (RoCEv2) ✓ 本次使用
```

`NCCL_IB_GID_INDEX=3` 读到空 GID → QP 建链失败。这也是当日 01:57 引擎
`TimeoutError: RPC call to sample_tokens timed out → EngineDeadError` 崩溃的可信诱因。

## 3. 修复

1. **验证 RoCEv2 @ GID index 4 可通**（双机直连）：
   ```bash
   # Node 0
   ib_write_bw -d rocep1s0f0 -x 4 -s 1M -n 1000 -F
   # Node 1
   ib_write_bw -d rocep1s0f0 -x 4 -s 1M -n 1000 -F 192.168.100.10
   # 结果：BW average ≈ 13013 MB/s（13 GB/s，与历史验收一致）；GID 192.168.100.10 ↔ .11
   ```
2. **双机 env 文件 `IB_GID_INDEX` 3 → 4**（改前已备份）：
   ```bash
   cd ~/deepseek-v4-vllm/upstream
   # 备份见：.env.canary64.bak-20260902-144059 / .env.canary128.bak-20260902-144059
   # 修改 .env.canary64 与 .env.canary128 中 IB_GID_INDEX=3 → IB_GID_INDEX=4
   ```
   compose.yaml 将其映射为容器环境变量 `NCCL_IB_GID_INDEX`。
3. **按文档顺序重启双机**（先 Node1 rank1，后 Node0 rank0）：
   ```bash
   # Node 1 / Node 0 各自执行
   cd ~/deepseek-v4-vllm/upstream
   docker compose --env-file .env.canary128 down
   docker compose --env-file .env.canary128 up -d
   ```
4. **验证**：
   - Node 0 `:18090/health` → 200（约 2 分钟就绪）。
   - 容器日志确认 `world_size=2`、模型加载、DSpark/FlashInfer 正常。
   - 直连 chat → `VLLM_DGX_OK`；LiteLLM HTTP :4000 chat → `GATEWAY_OK`；HTTPS :4443 `/v1/models` 正常；
     本机客户端经 `http://172.19.9.104:4000/v1` 实测通过。

## 4. 预防/后续建议

- **启动前先核对 GID index**（避免再次以错索引空跑）：
  ```bash
  for n in /sys/class/infiniband/rocep1s0f0/ports/1/gids/{3,4}; do
    echo "$n => $(cat $n)"
  done
  # 期望 index 4 = ::ffff:c0a8:640a（Node0） / ::ffff:c0a8:640b（Node1）
  ```
  若 GID 布局再次变化，以 sysfs 中“type=RoCE v2 且值为 ::ffff:c0a8:64xx”的实际 index 为准，
  同步修改双机 `.env.canary64/.env.canary128` 的 `IB_GID_INDEX`。
- 容器 `restart=no` 是刻意配置：本次故障后不会自动恢复，健康检查应显式检查
  `:18090/health` 并告警。遗留的 `deepseek-v4-healthcheck.service/.timer` 与
  `deepseek-v4-warmup.service`（NIM 时代，脚本路径已失效、每 5 分钟 203/EXEC 空转）
  **已于 2026-09-02 清除**（`systemctl disable` + 删除单元 + `daemon-reload`，
  空目录 `/opt/deepseek-v4` 一并删除）；如需自动恢复/告警，请另建检查 litellm + vLLM 的单元。
- `nvidia-smi` 在此平台（GB10 统一内存）显示 Memory N/A、GPU-Util ~96% 是桌面合成
  （gnome-shell/Xwayland）所致，不代表有推理负载，勿据此误判。

---

## 5. 复发记录（2026-09-02 07:57Z，约 1 小时后再次宕机）

GID 修复并验证通过约 1 小时后，vLLM 再次崩溃退出（exit 0，restart=no 不自愈），
LiteLLM 再次 500。

### 现象
```
Worker_TP0: RuntimeError: Worker failed with error 'CUDA error: CUBLAS_STATUS_INTERNAL_ERROR
  when calling `cublasGemmEx(... CUDA_R_16BF ... CUBLAS_GEMM_DEFAULT_TENSOR_OP)`'
→ EngineDeadError → 容器退出
```

### 时间线（UTC，取自容器日志）
| 时间 | 事件 |
|---|---|
| 07:56:42 | 请求触发 Triton 内核 **在线 JIT 编译** `_gather_shared_paged_supertile_kernel`（warmup 未覆盖的形状） |
| 07:56:47–07:57:07 | 一个短请求正常完成（KV 0.5%） |
| 07:57:18–38 | **新请求长上下文预填充，GPU KV cache 升至 ~34.7%（≈12.6 万 token，逼近 131072 上限）** |
| 07:57:47 | 大 shape 下 `cublasGemmEx` 抛 CUBLAS_STATUS_INTERNAL_ERROR → 引擎崩溃 |

### 结论（与第 1 次故障**非同一原因**）
- 第 1 次：**网络层** — RoCE GID 表漂移致 NCCL 建链失败（§2）。
- 第 2 次（本次）：**计算层 / 长上下文稳定性** — 接近 128K 的长上下文预填充触发
  cuBLAS GEMM 内部错误。旁证：崩溃前有 warmup 未覆盖形状导致的**在线 Triton JIT**，
  与迁移验收记录里「sparse_mla / 长上下文 CUDA 崩溃需跑 40K/64K/128K 矩阵验证」（P14）
  的风险同源。无 NVRM OOM（dmesg 里两次 OOM 均在 06:42Z 启动占显存时，与崩溃无关）。

### 处置
1. 双机 `docker compose --env-file .env.canary128 down && up -d`（先 Node1 后 Node0），
   约 2 分钟就绪；`:18090/health=200`，直连与网关小请求均通过，本机客户端实测正常。

### 建议（待办）
- **避免在 128K 逼近上限的请求上直接压测/使用**，先小步验证（40K→64K→96K→128K），
  确认该 pinned build（r16）长上下文边界；必要时联系上游 / 回退 canary64（MAX_MODEL_LEN=65536）。
- 长上下文形状不在 warmup 覆盖内（JIT 在线编译），可向镜像提供方反馈补充 warmup /
  修复 `gather_shared_paged_supertile` 大 shape 的 cuBLAS 路径。
- restart=no 为刻意配置；如需自愈可加「异常退出（非健康超时）才自动拉起」的守护，
  仍不应因单次健康超时重启双机。
