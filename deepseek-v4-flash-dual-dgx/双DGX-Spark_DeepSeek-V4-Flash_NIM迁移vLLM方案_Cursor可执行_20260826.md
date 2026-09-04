# 双 DGX Spark + DeepSeek-V4-Flash-0731
# NIM → vLLM 迁移部署方案（Cursor 可直接执行）

> 版本：v1.0  
> 日期：2026-08-26  
> 目标：在**完整保留当前 NIM Golden Baseline 和回滚能力**的前提下，将 DeepSeek-V4-Flash-0731 迁移到针对 2×DGX Spark/GB10/SM121 固定版本的 vLLM 运行时，优先解决当前约 40K prompt 触发 `sparse_mla_sm120_prefill.cu` CUDA crash 的 P0 问题，并为 Claude Code 建立至少 64K 级稳定上下文。

---

## 1. 当前基线

### 1.1 现有双 DGX Spark

| 项目 | Node 0 / API | Node 1 / Worker |
|---|---|---|
| 管理 IP | `172.19.9.104` | `172.19.49.159` |
| 主机 | `cube-fe5e` | `cube-0137` |
| GPU | DGX Spark GB10 / SM121 | DGX Spark GB10 / SM121 |
| CX-7 接口 | `enp1s0f0np0` | `enp1s0f0np0` |
| RoCE HCA | `rocep1s0f0` | `rocep1s0f0` |
| GID index | `3` | `3` |
| 直连 IP | `192.168.100.10/24` | `192.168.100.11/24` |
| 链路 | 200 Gb/s | 200 Gb/s |
| 运维账号 | `dgxdeploy` | `dgxdeploy` |

现有 CX-7、RDMA、NCCL 已验收通过，迁移阶段**禁止重新配置网络**。

### 1.2 当前 NIM Golden Baseline

```text
Image:
nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant

Node0 container:
deepseek-v4-rank0

Node1 container:
deepseek-v4-rank1

API:
http://172.19.9.104:8000/v1
```

当前 warm baseline：

```text
1K:   19.6 tps
8K:   21.9 tps
18K:  22.4 tps
C2:   27.7 aggregate tps
24K prefix cache: TTFT 18.14s → 0.39s
```

### 1.3 当前 P0

```text
~24K actual prompt: PASS
~40K prompt: CUDA crash

CUDA error at sparse_mla_sm120_prefill.cu:67: operation not permitted
Fatal Python error: Aborted
scheduler_0 exit -6
```

因此迁移首要目标是：

```text
40K PASS
64K PASS
0 CUDA crash
```

而不是一开始追求 1M context 或最高 tok/s。

---

## 2. vLLM 路线选择

不采用：

```text
pip install -U vllm
vllm latest
flashinfer latest
```

采用固定的 GB10/SM121 双机运行时。

### 2.1 第一推荐 canary runtime

```text
ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731
```

固定 manifest：

```text
sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b
```

完整引用：

```text
ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731@sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b
```

当前公开固定组合包含：

```text
ARM64 / GB10 SM12.1
CUDA 13.2
PyTorch 2.13.0+cu132
vLLM result tree a47a2f80278ef728f143ad851b08ac5b5001a0ac
FlashInfer 1ac6942776b383c6b03c7a5805a22e72a3e3349f
TP=2 / DCP=1
```

### 2.2 固定模型 revision

```text
model:
deepseek-ai/DeepSeek-V4-Flash-0731

revision:
9e165c30e2704aec5d9d593cce3eebd58bbef1cb
```

禁止使用 floating `main/latest`。

### 2.3 为什么先以 64K 为生产资格线

该 vLLM 双 Spark 路线公开配置支持 262K request limit，并已对 65,536 token 级场景做过持续解码验证；另一套双 DGX Spark vLLM 配方还公开了 40K profile、131K cold prefill 和 1M configured model length。

但本项目迁移不直接把这些社区结果视为本机保证。第一阶段只要求：

```text
64K context
+ tool calling
+ reasoning
+ prefix cache
+ 8h soak
```

128K 以上进入后续 canary。

---

## 3. 总体迁移架构

### Canary 阶段

```text
NIM Golden Baseline
      │
      │ stop（不删除）
      ▼
释放 UMA
      │
      ▼
vLLM Canary :18090
      │
      ▼
TP=2 / RoCE
 ┌────┴────┐
Node0     Node1
```

由于当前 NIM 每节点约占 113/121 GiB，**NIM 和 vLLM DeepSeek 不允许同时常驻**。

### 正式生产建议

```text
Claude Code ─┐
OpenCode ────┼──> LiteLLM :4000
Open WebUI ──┘          │
                         ▼
                    vLLM :18090
                         │
                    TP=2 / RoCE
                 ┌───────┴───────┐
                 ▼               ▼
              Node0            Node1
```

建议最终让客户端只访问 Gateway，不直接耦合底层 serving 端口。

---

## 4. Cursor 执行目录

Node 0：

```text
/home/dgxdeploy/deepseek-v4-vllm/
├── upstream/
├── state/
├── reports/
├── rollback/
├── benchmarks/
└── scripts/
```

Node 1 同路径。

---

## 5. Phase 0：冻结 NIM Golden Baseline

**此阶段不停止 NIM。**

Node 0：

```bash
mkdir -p ~/deepseek-v4-vllm/{state,reports,rollback,benchmarks,scripts}

docker inspect deepseek-v4-rank0 \
  > ~/deepseek-v4-vllm/rollback/nim-rank0-inspect.json

docker exec deepseek-v4-rank0 env | sort \
  > ~/deepseek-v4-vllm/rollback/nim-rank0-env.txt

docker top deepseek-v4-rank0 -eo pid,args \
  > ~/deepseek-v4-vllm/rollback/nim-rank0-process.txt
```

Node 1 同样保存 rank1。

同时保存：

```bash
bash /opt/deepseek-v4/ops/scripts/status.sh \
  > ~/deepseek-v4-vllm/rollback/nim-status.txt
```

禁止：

```text
docker rm deepseek-v4-rank*
docker rmi NIM image
rm -rf /opt/deepseek-v4/nim-cache
```

---

## 6. Phase 1：Preflight

两端执行：

```bash
hostname
uname -a
uname -m
nvidia-smi
free -h
df -hT
docker version
docker compose version
ip -br addr
ibdev2netdev
show_gids
rdma link
```

必须满足：

```text
uname -m = aarch64
```

Node0：

```bash
ping -c 3 192.168.100.11
ethtool enp1s0f0np0 | grep -E 'Speed|Link detected'
```

Node1：

```bash
ping -c 3 192.168.100.10
ethtool enp1s0f0np0 | grep -E 'Speed|Link detected'
```

必须仍为：

```text
200000Mb/s
Link detected: yes
```

---

## 7. Phase 2：检查磁盘

执行：

```bash
df -hT
lsblk -f
du -sh /opt/deepseek-v4/nim-cache 2>/dev/null || true
```

新 vLLM 路线建议每节点准备：

```text
model:      180 GiB 预算
runtime:     30 GiB 预算
JIT/cache:   30~50 GiB
安全余量:    20+ GiB
```

推荐额外可用空间：

```text
≥ 250 GiB / node
```

如果磁盘不足，Cursor **停止迁移**，先输出存储规划，不删除 NIM cache。

---

## 8. Phase 3：判断 NIM 权重能否复用

不要假设 `/opt/deepseek-v4/nim-cache` 可以直接给 vLLM。

Cursor 检查：

```bash
find /opt/deepseek-v4/nim-cache -maxdepth 6 \
  \( -name config.json \
  -o -name tokenizer.json \
  -o -name 'model*.safetensors' \
  -o -name model.safetensors.index.json \) \
  2>/dev/null | head -100
```

只有一个目录同时具备完整 HF snapshot 所需文件，且确认：

```text
model = deepseek-ai/DeepSeek-V4-Flash-0731
revision = 9e165c30...
```

并通过 checksum/revision 验证，才能复用。

否则必须单独准备固定 checkpoint。

---

## 9. Phase 4：准备模型

推荐路径：

```text
/data/models/DeepSeek-V4-Flash-0731
```

若 `/data` 不存在，Cursor 根据容量最大的本地文件系统选择路径。

Node0：

```bash
python3 -m venv ~/hf-dsv4
~/hf-dsv4/bin/pip install -U huggingface_hub
~/hf-dsv4/bin/hf auth login
```

下载：

```bash
~/hf-dsv4/bin/hf download \
  deepseek-ai/DeepSeek-V4-Flash-0731 \
  --revision 9e165c30e2704aec5d9d593cce3eebd58bbef1cb \
  --local-dir /data/models/DeepSeek-V4-Flash-0731
```

沿用现有已验证代理策略，不修改 CX-7/NCCL。

Node0 → Node1 建议走 CX-7：

```bash
rsync -ah --info=progress2 --partial \
  /data/models/DeepSeek-V4-Flash-0731/ \
  dgxdeploy@192.168.100.11:/data/models/DeepSeek-V4-Flash-0731/
```

最终必须验证两节点文件/checksum 一致。

---

## 10. Phase 5：拉取固定 vLLM Runtime

两节点：

```bash
docker pull \
ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731@sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b
```

检查 RepoDigest，两节点必须相同。

禁止替换成：

```text
:latest
```

---

## 11. Phase 6：Clone 配方并冻结

两节点：

```bash
cd ~/deepseek-v4-vllm
git clone https://github.com/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731.git upstream
cd upstream
git rev-parse HEAD | tee ../state/upstream-commit.txt
```

本轮迁移后不再 `git pull`。

---

## 12. Phase 7：Node 配置

### Node0 `.env.canary64`

基于 upstream `.env.example` 创建。

关键值：

```env
NODE_RANK=0
MASTER_ADDR=192.168.100.10
VLLM_HOST_IP=192.168.100.10
FABRIC_IFACE=enp1s0f0np0
IB_HCA=rocep1s0f0
IB_GID_INDEX=3
MODEL_HOST_PATH=/data/models/DeepSeek-V4-Flash-0731
CACHE_HOST_PATH=/data/cache/deepseek-v4-vllm
API_PORT=18090
MASTER_PORT=29501
```

### Node1 `.env.canary64`

```env
NODE_RANK=1
MASTER_ADDR=192.168.100.10
VLLM_HOST_IP=192.168.100.11
FABRIC_IFACE=enp1s0f0np0
IB_HCA=rocep1s0f0
IB_GID_INDEX=3
MODEL_HOST_PATH=/data/models/DeepSeek-V4-Flash-0731
CACHE_HOST_PATH=/data/cache/deepseek-v4-vllm
API_PORT=18090
MASTER_PORT=29501
```

Cache：

```bash
sudo mkdir -p /data/cache/deepseek-v4-vllm
sudo chown -R 1000:1000 /data/cache/deepseek-v4-vllm
```

---

## 13. Phase 8：Canary 64K 参数

upstream 固定 profile 包含类似：

```text
GPU_MEMORY_UTILIZATION=0.86
KV_CACHE_MEMORY_BYTES=20000000000
MAX_MODEL_LEN=262144
MAX_NUM_BATCHED_TOKENS=8192
MAX_NUM_SEQS=16
ENABLE_PREFIX_CACHING=1
```

但本项目第一轮应收敛到：

```env
MAX_MODEL_LEN=65536
MAX_NUM_SEQS=2
MAX_NUM_BATCHED_TOKENS=8192
ENABLE_PREFIX_CACHING=1
```

其他关键 runtime/kernel 参数保持 upstream pinned 默认。

不要自行切换 sparse attention/MoE backend。

### 为什么第一轮只做 64K

因为迁移首要目标是证明：

```text
原 NIM 40K crash 消失
Claude Code 得到实用长上下文
```

64K 已足以完成这一判断。

---

## 14. Phase 9：停止 NIM 前最后快照

```bash
curl -fsS http://172.19.9.104:8000/v1/models \
  > ~/deepseek-v4-vllm/rollback/nim-last-models.json

date -Is > ~/deepseek-v4-vllm/rollback/nim-stop-time.txt
```

---

## 15. Phase 10：停止 NIM

Node0：

```bash
docker stop deepseek-v4-rank0
```

Node1：

```bash
docker stop deepseek-v4-rank1
```

检查：

```bash
free -h
docker ps
```

必须确认 UMA 已显著释放。

**不要删除 NIM 容器。**

---

## 16. Phase 11：启动 vLLM Canary

当前固定 recipe 要求 **Rank1 先启动**。

Node1：

```bash
cd ~/deepseek-v4-vllm/upstream

docker compose --env-file .env.canary64 config --quiet
docker compose --env-file .env.canary64 pull
docker compose --env-file .env.canary64 up -d
```

查看：

```bash
docker compose --env-file .env.canary64 logs --tail=100
```

随后 Node0：

```bash
cd ~/deepseek-v4-vllm/upstream

docker compose --env-file .env.canary64 config --quiet
docker compose --env-file .env.canary64 up -d
```

跟踪：

```bash
docker compose --env-file .env.canary64 logs -f
```

首次会经历：

```text
model load
kernel compilation
FlashInfer autotuning
CUDA graph capture
```

禁止因为数分钟无 API 就反复 restart。

---

## 17. Phase 12：分布式验收

检查日志：

```bash
docker compose --env-file .env.canary64 logs 2>&1 \
  | grep -E 'NET/IB|NET/Socket|NCCL|world_size|rank'
```

必须确认：

```text
world_size=2
TP=2
NCCL 使用 NET/IB
```

如果主要落到 `NET/Socket`：

```text
FAIL
```

即使 API 能启动也不得进入性能测试。

---

## 18. Phase 13：API Smoke Test

Node0：

```bash
curl -fsS http://127.0.0.1:18090/health
curl -fsS http://127.0.0.1:18090/v1/models
```

外部：

```bash
curl -fsS http://172.19.9.104:18090/v1/models
```

Chat：

```bash
curl -sS http://172.19.9.104:18090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"deepseek-v4-flash-0731",
    "messages":[{"role":"user","content":"Reply exactly: VLLM_DGX_OK"}],
    "temperature":0,
    "max_tokens":32
  }'
```

---

## 19. Phase 14：上下文 P0 验证

这是迁移最核心的阶段。

按 **actual tokenizer tokens** 测：

```text
8K
16K
24K
32K
40K
48K
64K
```

每级：

```text
warmup 1
正式 3
```

记录：

```text
TTFT
prefill tok/s
decode tok/s
E2E
UMA
rank health
CUDA errors
```

### 40K 硬性 DoD

必须：

```text
40K × 3 PASS
0 CUDA crash
0 scheduler restart
0 rank disconnect
```

否则不能认为原 P0 已解决。

### 64K Claude Code 资格线

必须：

```text
64K × 3 PASS
64K input + 4K output PASS
```

通过后才能进入 Claude Code 长上下文集成。

---

## 20. Phase 15：Prefix Cache

构造：

```text
40K shared prefix + Q1
40K same prefix   + Q2
```

比较：

```text
TTFT cold
TTFT cache-hit
```

必须验证 `ENABLE_PREFIX_CACHING=1` 有真实收益。

---

## 21. Phase 16：Tool Calling

不能只看文本生成。

测试链：

```text
ls
→ read file
→ write temp file
→ shell
→ git diff
```

要求：

```text
tool_choice=auto
HTTP 200
tool JSON 正确
无 parser crash
无重复错误调用
无乱码
```

---

## 22. Phase 17：Reasoning

分别测试：

```text
thinking=false
thinking=true
reasoning_effort=high/max
```

保存 request/response/usage，确认客户端契约可用。

---

## 23. Phase 18：输出正确性

vLLM 迁移不能只以“不崩”和 HTTP 200 为标准。

固定 sanity corpus：

```text
Rust
Python
JSON
中文
英文
tool call
长代码生成
```

检查：

```text
无 token corruption
无重复乱码
JSON 可解析
代码完整
```

---

## 24. Phase 19：Benchmark

64K 稳定后再测性能。

### C1 Coding

```text
8K prompt
4K output
concurrency=1
```

### C2 Coding

```text
8K prompt
2K output
concurrency=2
```

### Long Prefill

```text
40K
64K
```

与现有 NIM Golden Baseline 对比：

| Case | NIM | vLLM | Change |
|---|---:|---:|---:|
| 1K | 19.6 tps |  |  |
| 8K | 21.9 tps |  |  |
| ~18K | 22.4 tps |  |  |
| C2 | 27.7 agg |  |  |
| 40K | CRASH |  |  |
| 64K | unavailable |  |  |

**40K/64K 稳定性高于吞吐优先级。**

---

## 25. Phase 20：Soak Test

先 2 小时：

```text
8K/24K/40K/64K 混合
并发 1~2
tool calling 周期性执行
```

要求：

```text
0 CUDA crash
0 OOM
0 NCCL failure
0 rank restart
0 corrupt response
```

通过后再 8 小时 soak。

只有 8h PASS 才能正式迁移。

---

## 26. Phase 21：128K Canary

只有满足：

```text
64K PASS
8h soak PASS
```

才创建 `.env.canary128`：

```env
MAX_MODEL_LEN=131072
MAX_NUM_SEQS=1
```

依次测试：

```text
64K
96K
128K
```

每级 3 次。

256K/1M **不属于本次迁移 DoD**。

---

## 27. Claude Code 上下文建议

### 如果 backend 64K PASS

Gateway/Context Guard 推荐：

```text
warning:       40K
compact:       48K
hard guard:    56K
backend limit: 64K
```

### 如果 backend 128K PASS

```text
warning:       80K
compact:       96K
hard guard:    112K
backend limit: 128K
```

Claude Code 本身走 Anthropic Messages API，而 vLLM 暴露 OpenAI-compatible API，因此生产阶段使用：

```text
Claude Code
   ↓
LiteLLM / Anthropic-compatible gateway
   ↓
vLLM
```

第一阶段不要先上 LiteLLM；先把 vLLM 64K/tool/reasoning/stability 验收完。

---

## 28. 回滚 NIM

任何阶段失败都必须执行回滚。

停止 vLLM：

```bash
cd ~/deepseek-v4-vllm/upstream
docker compose --env-file .env.canary64 down
```

确认 UMA 释放。

恢复 NIM：

Node0：

```bash
docker start deepseek-v4-rank0
```

等待 Rank0 正常后，Node1：

```bash
docker start deepseek-v4-rank1
```

验收：

```bash
curl -f http://172.19.9.104:8000/v1/models
bash /opt/deepseek-v4/ops/scripts/status.sh
```

回滚禁止：

```text
删除 vLLM model
删除 NIM cache
修改 CX-7
修改 netplan
重装驱动/CUDA
```

---

## 29. 如果 vLLM 40K 仍崩

立即保存：

```bash
docker compose logs --no-color > ~/deepseek-v4-vllm/reports/vllm-40k-crash.log
dmesg -T | grep -Ei 'Xid|NVRM|CUDA' > ~/deepseek-v4-vllm/reports/kernel.log
```

然后进入 **Canary-B**：

```text
关闭 DSpark/speculative decoding
```

重新测试：

```text
24K
40K
64K
```

若关闭 speculative 后 PASS：

```text
优先使用稳定模式生产
```

再评估 decode tps 损失。

若仍失败，再比较第二套 pinned 双 DGX Spark vLLM runtime，例如 `m9e/deepseek-v4-flash-0731-2x-dgx-spark`，不要直接回到存在相同 Sparse MLA P0 的裸 SGLang 路线。

---

## 30. 正式生产端口建议

Canary：

```text
vLLM :18090
```

正式生产仍建议保持：

```text
vLLM :18090
LiteLLM :4000
```

客户端：

```text
Claude Code
OpenCode
Open WebUI
```

统一访问 Gateway。

不要让各客户端直接依赖 NIM/vLLM 端口，后续可无感切换 backend。

---

## 31. 最终迁移 DoD

只有全部满足才允许输出：

```text
VLLM MIGRATION PASS
```

### 基础

- [ ] Runtime digest 固定
- [ ] Model revision 固定
- [ ] 两节点模型 checksum 一致
- [ ] TP=2
- [ ] NCCL `NET/IB`
- [ ] API PASS

### 上下文

- [ ] 24K PASS
- [ ] 32K PASS
- [ ] 40K PASS ×3
- [ ] 48K PASS
- [ ] 64K PASS ×3
- [ ] 64K + 4K output PASS
- [ ] 0 CUDA crash

### 编程

- [ ] Tool calling PASS
- [ ] JSON PASS
- [ ] Rust/Python PASS
- [ ] 中英文 PASS
- [ ] 无乱码/token corruption

### Reasoning

- [ ] Fast/non-thinking PASS
- [ ] Thinking PASS
- [ ] High/max PASS

### Cache

- [ ] Prefix cache PASS
- [ ] Cache-hit TTFT 有明显收益

### 稳定性

- [ ] C1 PASS
- [ ] C2 PASS
- [ ] 2h soak PASS
- [ ] 8h soak PASS
- [ ] 0 OOM
- [ ] 0 rank disconnect
- [ ] 0 NCCL failure

---

## 32. Cursor 总任务 Prompt

```text
目标：
在不破坏当前 NIM Golden Baseline 的前提下，为两台 DGX Spark 建立
DeepSeek-V4-Flash-0731 pinned vLLM canary，优先解决约 40K prompt 时
sparse_mla_sm120_prefill.cu CUDA crash，并验证至少 64K 上下文以供 Claude Code 使用。

当前：
Node0 172.19.9.104 / CX7 192.168.100.10
Node1 172.19.49.159 / CX7 192.168.100.11
interface=enp1s0f0np0
HCA=rocep1s0f0
GID=3
user=dgxdeploy

NIM：
deepseek-v4-rank0 / deepseek-v4-rank1
API=http://172.19.9.104:8000/v1
必须完整保留，不删除。

vLLM runtime：
ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731@sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b

模型：
deepseek-ai/DeepSeek-V4-Flash-0731
revision=9e165c30e2704aec5d9d593cce3eebd58bbef1cb

禁止：
- latest
- pip install -U vllm/flashinfer
- 改 NVIDIA driver/CUDA
- 改 CX7/netplan/RDMA/NCCL
- 删除 NIM image/cache/container
- NIM 与 vLLM DeepSeek 同时常驻

执行：
1. Snapshot 当前 NIM inspect/env/process/status。
2. Preflight aarch64/GPU/Docker/RDMA/CX7。
3. 检查每节点至少约 250GiB 额外存储；不足则停止。
4. 检查 NIM cache 是否是可复用的完整固定 HF snapshot；无法确认则下载固定 revision。
5. 两节点模型文件/checksum 一致。
6. Pull 固定 vLLM image digest，两节点 RepoDigest 一致。
7. Clone liquidgravityai recipe，记录 git rev-parse HEAD 并冻结。
8. 配置 Node0：rank0/master=192.168.100.10/local=192.168.100.10/interface/HCA/GID=已知值。
9. 配置 Node1：rank1/master=192.168.100.10/local=192.168.100.11。
10. Canary API port=18090。
11. Canary64：MAX_MODEL_LEN=65536、MAX_NUM_SEQS=2、MAX_NUM_BATCHED_TOKENS=8192、prefix caching on；其他 kernel/runtime 参数保持 pinned recipe 默认。
12. 保存 NIM 最后状态，然后 stop NIM（不删除）。
13. 确认 UMA 释放。
14. 先启动 vLLM rank1，再启动 rank0；首次 JIT/autotuning 不得反复 restart。
15. 验证 TP=2、world_size=2、NCCL NET/IB、/health、/v1/models、chat。
16. 按 actual token 测 8K/16K/24K/32K/40K/48K/64K。
17. 40K、64K 各至少正式 3 次；任何 CUDA crash 保存 logs/dmesg 并停止继续加长度。
18. 40K ×3 PASS 才认为原 NIM P0 被绕开。
19. 64K ×3 + 64K input/4K output PASS 后才允许 Claude Code 资格测试。
20. 验证 40K shared-prefix cache hit。
21. 验证 tool_choice=auto：ls/read/write/bash/git diff。
22. 验证 thinking=false、thinking=true、reasoning effort high/max。
23. 固定 Rust/Python/JSON/中文/英文 corpus 检查无 silent corruption。
24. 然后 benchmark 8K c1/c2、40K、64K，并与 NIM baseline 对比。
25. 2h soak PASS 后做 8h soak；必须 0 CUDA crash/0 OOM/0 NCCL failure/0 rank restart/0 corrupt output。
26. 只有 64K+8h PASS 后再做 canary128：64/96/128K。
27. 256K/1M 不属于本次迁移 DoD。
28. 任何失败：down vLLM，释放 UMA，恢复 NIM rank0→rank1，验证原 API。
29. 最终生成 reports/vllm-migration-report.md，包含 runtime/model pin、上下文矩阵、tool/reasoning/cache、性能、soak、与 NIM 对比、Claude 推荐 context、回滚验证和是否建议正式切换。
```

---

## 33. 参考实现

### 主迁移配方

https://github.com/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731

其公开内容提供：

- 2×DGX Spark / GB10 SM121
- official DeepSeek-V4-Flash-0731 revision
- TP=2 / RoCE
- 固定 vLLM / FlashInfer / B12X runtime
- immutable image digest
- prefix caching
- 65,536 级 sustained-context 验证
- 262,144 configured request limit
- structured DSpark request 验证

### 长上下文交叉验证

https://github.com/m9e/deepseek-v4-flash-0731-2x-dgx-spark

公开报告：

- official 0731 checkpoint
- 2×DGX Spark TP=2/PP=1
- RoCEv2
- 40K speed profile
- 131K cold prefill
- 1M configured model length
- prefix caching
- reasoning/tool encoding

这些社区结果用于证明迁移方向具有工程可行性，不替代本机 canary/soak 验收。

---

## 34. 最终判断标准

本次工作不是简单“把 NIM 换成 vLLM”，而是必须证明：

```text
1. 40K CUDA crash 消失
2. 至少 64K 稳定
3. Tool calling 正常
4. Reasoning 正常
5. Prefix cache 正常
6. 输出无 silent corruption
7. 8 小时无故障
8. Claude Code 可安全使用
9. 随时可恢复现有 NIM
```

只有满足以上条件，才进行正式生产切换。
