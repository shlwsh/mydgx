# 技术汇报：DGX Spark 上 Qwen3.6-35B-A3B-NVFP4 长时运行静默挂死

> **对象**：NVIDIA 工程师技术沟通  
> **日期**：2026-11-09  
> **状态**：待厂商确认根因与规避方案  
> **平台**：DGX Spark 工作站  
> **模型**：`Qwen3.6-35B-A3B-NVFP4`（NVFP4 量化 MoE）  
> **推理镜像**：`vllm/vllm-openai:qwen3.6`  
> **业务**：WiNBOT 病历质控（山西晋城试点，心内科、骨科）  
> **当前恢复手段**：重启模型服务。尚无稳定根治办法。

---

## 1. 沟通目标

请协助确认以下三点，并给出可在现网执行的处理建议：

1. 该现象是否为 **NVFP4 MoE + 当前 vLLM / CUDA Graph / FlashInfer** 路径上的已知缺陷（Silent Engine Hang）。
2. 在 **不更换业务模型** 的前提下，推荐的启动参数、镜像版本或补丁。
3. 假死发生时，建议采集哪些证据（GPU 状态、EngineCore 栈、CUDA 同步点），以便一次定位。

---

## 2. 问题摘要

服务在持续质控负载下运行一段时间后进入**假死**：

- 推理请求（`POST /v1/chat/completions`）挂起，客户端长时间无返回。
- 引擎周期统计中 **generation throughput 降为 0**，但 Running / Waiting 请求仍在。
- 容器日志**没有明确异常栈**，进程往往仍在，不是干净退出。
- `/health`、`/v1/models` 仍可能返回 **HTTP 200**，不能代表推理可用。
- **重启服务后恢复**。随运行时间再次出现。

这与公开分析中的 **Silent Engine Hang** 描述一致：请求已进入 HTTP 层，EngineCore 不再产出 token；底层怀疑为 NVFP4 MoE 配合 FlashInfer 或 CUDA Graph 时，CUDA kernel 陷入死循环，或 `cuEventSynchronize` 永久阻塞。该分析多见于部分 Blackwell 平台上的 NVFP4 MoE，**是否覆盖 DGX Spark（GB10）需厂商确认**。

附图：

- [图 1 现场日志：正常吞吐后 generation 归零](assets/fig1_vllm_fake_hang_log.png)
- [图 2 Silent Engine Hang 分析摘录](assets/fig2_nvfp4_moe_silent_hang_analysis.png)

---

## 3. 运行环境

| 项 | 当前已知信息 | 待现场补齐 |
|---|---|---|
| 机器 | DGX Spark 工作站 | 主机名、GPU 型号 / 数量、显存与统一内存 |
| 模型 | `/models`，`Qwen3.6-35B-A3B-NVFP4` | 权重来源、revision、checksum |
| 对外名称 | `qwen3.6` | — |
| 镜像 | `vllm/vllm-openai:qwen3.6` | digest、vLLM / CUDA / FlashInfer 版本 |
| 驱动 | 未在本次材料中固定 | `nvidia-smi` 驱动与 CUDA 版本 |
| 监听 | `0.0.0.0:8001` | 是否经网关、是否对非业务网段可达 |
| 部署形态 | `vllm serve` | 容器名、重启方式、是否有 watchdog |

### 3.1 当前参考启动命令

```bash
vllm serve /models \
  --host 0.0.0.0 --port 8001 \
  --quantization ascend \
  --max-model-len 262144 \
  --max-num-seqs 12 \
  --max-num-batched-tokens 4096 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching \
  --trust-remote-code \
  --served-model-name qwen3.6 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --additional-config '{"enable_cpu_binding":true, "multistream_overlap_shared_expert": true}'
```

请厂商重点核对：

| 参数 | 我方关注点 |
|---|---|
| `--quantization ascend` | 在 `vllm/vllm-openai:qwen3.6` + DGX Spark 上是否为 NVFP4 的正确量化后端；若不是，正确取值是什么 |
| `cudagraph_mode=FULL_DECODE_ONLY` | 是否会提高 NVFP4 MoE 静默挂死概率；推荐模式是什么 |
| `enable_prefix_caching` | 长病历、高重复前缀场景下是否参与挂死 |
| `multistream_overlap_shared_expert` | MoE shared expert 多流重叠是否为已知风险项 |
| `max-model-len 262144` + `max-num-seqs 12` + `max-num-batched-tokens 4096` | 对该模型在 Spark 上的推荐上限 |

---

## 4. 业务负载（用于判断是否为长稳问题）

质控为持续在线批处理，输入以病历长文本为主。近期试点数据：

| 日期 | 患者 | 在院病历 | 归档病历 | 质控问题 | 问题病历 |
|---|---:|---:|---:|---:|---:|
| 2026-10-29 | 2046（+79） | 26825（+785） | 39488（+432） | 6342 | 2216 |
| 2026-11-05 | 2114（+68） | 27890（+1065） | 40025（+537） | 6610 | 2308 |

约一周净增：患者 68，在院病历 1065，归档病历 537。服务需要**多日连续推理**，不是短时 smoke。短请求可以成功，挂死出现在长时间运行之后。

---

## 5. 现场证据

### 5.1 正常段

来自业务地址的 `POST /v1/chat/completions` 持续 `200 OK`。引擎约每 10 秒打印一次统计，量级为：

- Avg prompt throughput：约 1300～3000 tokens/s
- Avg generation throughput：约 67～284 tokens/s
- Running：14（高于启动参数 `--max-num-seqs 12`，请帮助解释该计数口径）
- Waiting：0～8
- GPU KV cache usage：约 2.4%～2.5%
- Prefix cache hit rate：约 1.9%

KV 占用很低，**不像 KV 耗尽或显存打满**。

### 5.2 转折点

约 `16:03:07`：Running=14、Waiting=8 仍在，**Avg generation throughput = 0.0 tokens/s**。请求还在队列里，生成已经停住。

### 5.3 同期其他日志

同一时段另有来源对非推理路径做高频访问：大量 `Invalid HTTP request received`，以及对 `/phpMyAdmin/`、`/HNAP1`、`/solr/admin/...` 等路径的 `404`。

我方目前把这视为**并发观察到的暴露面问题**，不把它当成已证实的根因。需要厂商判断：API Server 处理大量非法 HTTP 时，是否会把 EngineCore 一起拖进不可恢复挂起；还是引擎挂死与扫描流量彼此独立。

### 5.4 与公开 Silent Hang 描述的对照

| 公开描述 | 我方现场 |
|---|---|
| `/health`、`/v1/models` 仍 200 | 符合。健康检查会误报正常 |
| chat 请求挂起，curl 无返回 | 符合 |
| 日志无错误栈，只是不再出 token | 符合。图 1 在归零后没有 Engine 异常栈 |
| NVFP4 MoE + FlashInfer / CUDA Graph | 模型与 `FULL_DECODE_ONLY` 相符；FlashInfer 版本未核对 |
| kernel 死循环或 `cuEventSynchronize` 永久阻塞 | **尚未在本机抓到调用栈**，不能当作已证实结论 |

---

## 6. 已做处理与边界

| 项 | 情况 |
|---|---|
| 恢复 | 重启模型服务可恢复，属于临时手段 |
| 根治 | 没有已验证的配置或补丁 |
| 自动摘除 | 没有。若只探测 `/health`，假死期间仍可能是 200 |
| 根因实验 | 尚未完成「关闭 CUDA Graph / 更换量化参数 / 更换镜像 digest」的对照 |

---

## 7. 请 NVIDIA 协助确认的问题

1. `Qwen3.6-35B-A3B-NVFP4` 在 **DGX Spark + `vllm/vllm-openai:qwen3.6`** 上，是否有已知的 Silent Engine Hang、CUDA Graph 死锁或 `cuEventSynchronize` 挂起？对应的 issue / 修复版本是什么？
2. 该权重在此镜像上，`--quantization` 的正确取值是什么？`ascend` 是否会被忽略、报错，或走到错误 kernel？
3. `cudagraph_mode` 的推荐值是什么？是否建议 `NONE` 或 `PIECEWISE` 作为规避？对吞吐的预期影响？
4. `multistream_overlap_shared_expert`、prefix caching、tool parser、reasoning parser 里，哪些应在稳定性优先时关闭？
5. 假死时 Engine 仍打印 Running/Waiting，但 generation 为 0。这更像 scheduler 卡住、decode kernel 卡住，还是 HTTP 与 Engine 之间的队列堵死？
6. 非法 HTTP 洪水是否可能单独造成这种「无栈、generation=0、health 仍 200」？还是只能作为干扰项排除？
7. 建议的采集命令：`nvidia-smi`、Xid、`cuda-gdb`/`nsys` 挂在哪个进程、vLLM 的哪一级日志（`VLLM_LOGGING_LEVEL`、EngineCore）。
8. 若短期没有补丁：推荐的生产配置（上下文长度、并发、graph、量化）是什么？是否必须改用其他精度？

---

## 8. 建议共同执行的验证（请排优先级）

在业务低峰、可回滚的窗口做 A/B，每组至少覆盖一次「长于以往平均挂死时间」的质控负载。

| 优先级 | 实验 | 成功标准 |
|---|---|---|
| P0 | 保持现网命令，假死**重启前**按第 9 节抓栈 | 拿到 Engine 阻塞点，而不是只看到 0 token/s |
| P0 | 核对镜像 digest、vLLM、CUDA、FlashInfer、驱动版本 | 能对应到已知问题或排除版本 |
| P1 | `cudagraph_mode` 改为关闭或厂商推荐值，其余不变 | 同等负载下不再挂死，或挂死时间显著后移 |
| P1 | 按厂商意见修正 `--quantization` | 服务能正确加载 NVFP4，且长跑稳定 |
| P2 | 关闭 `multistream_overlap_shared_expert` 与 prefix caching 做单因子对照 | 分清是哪一个开关相关 |
| P2 | 仅内网/网关暴露，去掉扫描流量后再观察 | 确认挂死是否仍复现 |
| P3 | 若 P1 无效，评估厂商推荐的镜像 tag 或非 NVFP4 路径 | 明确是否必须换权重或换运行时 |

业务侧兜底（与根因并行，不替代厂商修复）：

- 用真实短对话探测 `/v1/chat/completions`，超时即判不可用；不以 `/health` 为准。
- 探测失败后保留现场日志，再执行受控重启。

---

## 9. 下次挂死、重启前请保留的材料

1. 挂死前 30 分钟完整服务日志（含最后一条非 0 的 generation 统计）。
2. `nvidia-smi`、`nvidia-smi -q`、`dmesg` 中的 Xid / GPU fallen off。
3. EngineCore 与 API Server 的 Python / CUDA 调用栈。
4. 当时一条挂起的 chat 请求：开始时间、prompt 长度、是否带 tool / reasoning。
5. 镜像 `docker inspect` 的 Image ID / digest，以及驱动版本。
6. 启动命令全文与容器运行时长。

---

## 10. 我方当前判断（供会上对齐，不是定论）

1. 业务影响明确：质控长跑后推理停转，只能重启。
2. 现场形态符合 Silent Engine Hang：generation 为 0、无异常栈、健康检查不可信，且当时 KV 占用只有约 2.5%。
3. 最需要厂商确认的技术点是：**NVFP4 MoE 在本镜像上的量化参数、CUDA Graph 模式，以及 DGX Spark 上是否已有修复版本**。
4. 扫描流量需要治理，但是否导致引擎挂死，需要厂商从 API Server / EngineCore 线程模型上给判断。
5. 在补丁明确之前，现网只能维持「重启恢复」；希望本次沟通给出可执行的参数规避和取证步骤。

---

## 附：材料索引

| 材料 | 路径 |
|---|---|
| 图 1 现场日志 | [assets/fig1_vllm_fake_hang_log.png](assets/fig1_vllm_fake_hang_log.png) |
| 图 2 分析摘录 | [assets/fig2_nvfp4_moe_silent_hang_analysis.png](assets/fig2_nvfp4_moe_silent_hang_analysis.png) |
| 内部讨论稿（同题，偏内部评审） | [讨论报告_Qwen3.6-NVFP4_WiNBOT质控假死问题_20261109.md](讨论报告_Qwen3.6-NVFP4_WiNBOT质控假死问题_20261109.md) |
