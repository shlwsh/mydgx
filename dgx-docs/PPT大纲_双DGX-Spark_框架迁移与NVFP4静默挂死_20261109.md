# PPT 大纲：DgxSpark应用沟通交流

> **日期**：2026-11-09  
> **页数**：6 页（标题页 + 框架迁移 2 页 + 静默挂死 2 页 + 感谢页）  
> **每题结构**：首一页只放现状表现，次页只放总结的故障点及要求  
> **依据**：
>
> - `dgx-docs/汇报报告_双DGX-Spark_NIM到vLLM框架迁移分析_20261109.md`
> - `dgx-docs/汇报报告_Qwen3.6-NVFP4_SilentHang_英伟达技术沟通_20261109.md`

内容页左上角可标「DgxSpark应用沟通交流」。每页一句页眉结论，数字进表。标题页与感谢页不加栏目、不加数据。

## 总结构

| 页 | 角色 | 设计母题 | 这一页只放 |
|---|---|---|---|
| 1 | 标题页 | 全页居中，只有主标题 | DgxSpark应用沟通交流 |
| 2 | 框架迁移 · 现状 | 上下两段现场：迁移前 NIM / 迁移后 vLLM | 看得见的运行表现与验收数字 |
| 3 | 框架迁移 · 故障点及要求 | 左故障点、右要求 | 已换路的 P0、未关闭的 128K、对引擎与退路的要求 |
| 4 | 静默挂死 · 现状 | 四格现象 + 一条转折 | 质控长跑后的假死现场 |
| 5 | 静默挂死 · 故障点及要求 | 左待确认故障点、右对厂商的要求 | 可疑路径、取证与现网兜底 |
| 6 | 感谢页 | 全页居中，只有致谢 | 感谢 |

---

## 第 1 页 · 标题页

**设计母题：** 全页居中。只有主标题，无副标题、无日期、无议程、无 logo 说明文字。

**标题：** DgxSpark应用沟通交流

---

## 第 2 页 · 框架迁移：现状表现

**设计母题：** 上下两段，同为「现场表现」，不做根因结论。上段 NIM 停用前的基线，下段 2026-08-26 验收后的 vLLM 生产表现；页底一条现网约束。  
**页眉结论：** 同一套 DeepSeek-V4-Flash 权重，短上下文两边都能服务；NIM 在约 40K 崩溃，vLLM 验收到 64K，靠近 128K 仍会退出。

**页题：** 双 DGX Spark 上 NIM 与 vLLM 的现场表现

**共有事实（页题下一条）**

- 模型：`deepseek-ai/DeepSeek-V4-Flash-0731`，约 155G / FP8
- 权重 revision：`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`
- 硬件：2× DGX Spark GB10（SM121），TP=2，ConnectX-7 / RoCE
- 两边都是 OpenAI 兼容 chat API；各有 compose，不允许同时常驻

**上段 · NIM 1.11.0-variant（官方基线，现已停用，容器保留）**

| 项 | 现场 |
|---|---|
| 镜像 | `nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant` |
| 部署 | Node0 `deepseek-v4-rank0`，Node1 `deepseek-v4-rank1`，`:8000/v1` |
| 引擎 | 容器内 SGLang，prefill 为 sparse MLA；首次请求表现为 SGLang autotuning |
| 短上下文 | 约 24K 可通过；1K / 8K decode 为 19.6 / 21.9 tok/s |
| 内存 | 每节点约 113 / 121 GiB |
| 长上下文 | 大于 32K 触发崩溃；约 40K prompt 把调度进程打崩 |

崩溃现场原文：

```text
约 40K prompt → CUDA crash
CUDA error at sparse_mla_sm120_prefill.cu:67: operation not permitted
Fatal Python error: Aborted
scheduler_0 exit -6
```

**下段 · pinned vLLM（2026-08-26 验收后的生产运行时）**

| 项 | 现场 |
|---|---|
| 镜像 | `ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731`，本地 tag `pinned-676a1c89`（arm64，24.8GB） |
| digest | `sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b` |
| 组合 | CUDA 13.2 · PyTorch 2.13.0+cu132 · vLLM 0.26.1rc0 · dspark.r16 · FlashInfer `1ac69427…` · NCCL 2.30.4 · TP=2 / DCP=1 · 推测解码 spec=5 |
| 服务 | `:18090`，模型名 `deepseek-v4-flash-0731` |
| 验收 | 8K～64K 均为 3/3；真 64K 为 61509 tokens，3/3；冷 prefill 16.7s，共享前缀命中后 TTFT 0.7s |
| 吞吐 | 1K 47.3 tok/s（2.4 倍），8K 43.1 tok/s（2.0 倍）；TTFT 与 NIM 持平，约 0.3～0.4s |
| 功能 | tool calling、reasoning 通过；编程场景关闭 thinking |
| 内存 | 同样占满统一内存。切换时先停 NIM、不删容器，再起 vLLM |

**页底 · 验收之后仍观察到的表现**

| 日期 | 现场 |
|---|---|
| 2026-09-02 | KV 约 12.6 万 token（靠近 131072）时，`cublasGemmEx` 返回 `CUBLAS_STATUS_INTERNAL_ERROR`，EngineDeadError，进程 exit 0 |
| 2026-09-10 | 大请求 `sample_tokens` RPC 超时，同样 EngineDeadError、exit 0；单发约 113K 后来可以成功 |
| 现网 | 生产侧用 125K 输入硬顶挡住该区间；128K 未作为当时的生产资格线 |

---

## 第 3 页 · 框架迁移：故障点及要求

**设计母题：** 左右分栏。左栏三条故障点，按「已换路 / 未关闭 / 资格线未封口」纵向排列；右栏对运行时的要求，每条对应可执行约束。不重复第 2 页的吞吐表。  
**页眉结论：** 约 40K 的 SGLang abort 已用固定 vLLM 换路；靠近 128K 的退出还未在引擎侧修好。

**页题：** 框架迁移的故障点与后续要求

**左栏 · 故障点**

| # | 故障点 | 归纳 |
|---|---|---|
| 1 | NIM 约 40K abort | 硬件 GB10 / SM121，崩溃点在 SM120 sparse MLA prefill：`sparse_mla_sm120_prefill.cu:67`。kernel 打崩调度进程，调 KV 长度或并发绕不开。与裸 SGLang 的 Sparse MLA 同类。2026-08-26 起该路径已停用。 |
| 2 | pinned vLLM 靠近 128K 退出 | `pinned-676a1c89`（vLLM 0.26.1rc0 + dspark.r16）在靠近 131072 时另有计算错误：`cublasGemmEx`（BF16）内部错误，或 `sample_tokens` RPC 超时。进程 exit 0。原 `sparse_mla_sm120_prefill.cu` 没有复发。09-02 崩溃前还有 warmup 未覆盖的 Triton kernel 在线编译。 |
| 3 | 长稳资格线未封口 | 30 分钟 / 2 小时 / 8 小时 soak 当时没做完。约定是 64K 加 8 小时通过之后才允许 128K canary。后来的 128K 故障发生在这条线还没封口时。 |

**右栏 · 要求**

1. **生产运行时维持这版固定构建。** 不采用 `pip install -U vllm`、`vllm latest`、`flashinfer latest`。GB10 上 CUDA 13.2、FlashInfer、vLLM 提交必须一起匹配，否则长上下文无法复现，也不能和 NIM 的 40K 崩溃对照。配方 commit：`8a161c09f1269ee8ea18ba324b4682d0712f4e4a`。
2. **64K 作为已验收上下文。** 合格线是 40K 通过、64K 通过、0 次 CUDA crash，外加 tool calling、reasoning、prefix cache。社区材料中的 262K 请求上限、131K 冷 prefill、1M 配置长度，不作为本机保证。
3. **推测解码保持验收时的开态。** spec=5 在验收中开着并通过。退路仅在 40K 仍崩时使用：先关推测解码再测 24K / 40K / 64K；关了才通过，才改稳定模式。这条退路没有走到。
4. **退路排除裸 SGLang。** 若本版 vLLM 在长上下文上失败，换另一套 pinned vLLM，不退回裸 SGLang。
5. **125K 输入硬顶继续作为网关限制。** 它挡住靠近 128K 的退出区间，不是引擎修复。引擎侧未关闭前，不把 128K 当成生产资格。
6. **补做 soak 再谈 128K canary。** 顺序是先完成 64K 条件下的 30 分钟 / 2 小时 / 8 小时长稳，通过之后才允许 128K 探测。

---

## 第 4 页 · 静默挂死：现状表现

**设计母题：** 上半四格现象，下半「正常段 → 16:03:07」对照。健康检查 200 用警示色。本页只记观察到的事实，不把 kernel 死循环写成已证实原因。  
**页眉结论：** 质控长跑后生成吞吐归零，进程还在，日志没有异常栈；重启后恢复，随运行时间再次出现。

**页题：** Qwen3.6-NVFP4 在 WiNBOT 质控上的假死现场

**对象条**

- 平台：DGX Spark 工作站
- 模型：`Qwen3.6-35B-A3B-NVFP4`（NVFP4 量化 MoE），对外名 `qwen3.6`
- 镜像与启动：`vllm/vllm-openai:qwen3.6`，`vllm serve`，`0.0.0.0:8001`
- 业务：WiNBOT 病历质控，山西晋城试点，心内科、骨科
- 当前恢复手段：重启模型服务
- 状态：待厂商确认根因与规避方案

**四格现象**

| 请求 | 引擎统计 | 日志与进程 | 健康检查 |
|---|---|---|---|
| `POST /v1/chat/completions` 挂起，客户端长时间无返回 | generation throughput 降为 0；Running / Waiting 仍在 | 容器日志没有明确异常栈；进程往往仍在，不是干净退出 | `/health`、`/v1/models` 仍可能 HTTP 200，不能代表推理可用 |

**业务负载（短请求可以成功，挂死出现在长时间运行之后）**

| 日期 | 患者 | 在院病历 | 归档病历 | 质控问题 | 问题病历 |
|---|---:|---:|---:|---:|---:|
| 2026-10-29 | 2046（+79） | 26825（+785） | 39488（+432） | 6342 | 2216 |
| 2026-11-05 | 2114（+68） | 27890（+1065） | 40025（+537） | 6610 | 2308 |

约一周净增：患者 68，在院病历 1065，归档病历 537。服务需要多日连续推理。

**转折对照**

| | 正常段（约每 10 秒一条） | 约 16:03:07 |
|---|---|---|
| prompt throughput | 约 1300～3000 tokens/s | — |
| generation throughput | 约 67～284 tokens/s | **0.0 tokens/s** |
| Running / Waiting | Running 14（高于启动参数 `--max-num-seqs 12`）；Waiting 0～8 | Running=14，Waiting=8，请求仍在队列 |
| GPU KV cache | 约 2.4%～2.5% | 同样很低 |
| Prefix cache hit | 约 1.9% | — |

KV 占用很低，现场不像 KV 耗尽或显存打满。

**同期另观察到的流量（并列事实，未证实为根因）**

同一时段另有来源对非推理路径高频访问：大量 `Invalid HTTP request received`，以及对 `/phpMyAdmin/`、`/HNAP1`、`/solr/admin/` 等路径的 404。

**现网启动命令（现状，供次页对照，本页不评价对错）**

```text
vllm serve /models
  --host 0.0.0.0 --port 8001
  --quantization ascend
  --max-model-len 262144
  --max-num-seqs 12
  --max-num-batched-tokens 4096
  --gpu-memory-utilization 0.90
  --enable-prefix-caching
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}'
  --additional-config '{"enable_cpu_binding":true, "multistream_overlap_shared_expert": true}'
  --enable-auto-tool-choice
  --tool-call-parser qwen3_coder
  --reasoning-parser qwen3
  thinking 关闭
```

镜像 digest、vLLM / CUDA / FlashInfer、驱动版本、主机名与 GPU 数量，本次材料未固定。

---

## 第 5 页 · 静默挂死：故障点及要求

**设计母题：** 左栏总结故障点，每条标明「已观察到」或「待厂商确认」。右栏是对 NVIDIA 的要求：确认项、取证、验证顺序、现网兜底。不把未抓到的调用栈画成已定位。  
**页眉结论：** 现场形态符合 Silent Engine Hang；量化参数、CUDA Graph 和 GB10 上是否已有修复，需要厂商确认。补丁明确前，现网以真实对话探测加受控重启兜底。

**页题：** NVFP4 假死的故障点与对厂商的要求

**左栏 · 故障点**

| # | 故障点 | 现状归纳 |
|---|---|---|
| 1 | 静默挂死，健康检查失真 | 请求已进入 HTTP 层，EngineCore 不再产出 token。generation 为 0，无异常栈，`/health` 仍可能 200。重启可恢复，多日负载下复发。业务影响是质控推理停转。 |
| 2 | 可疑路径尚未钉死 | 公开描述为 NVFP4 MoE 配合 FlashInfer 或 CUDA Graph 时，kernel 死循环或 `cuEventSynchronize` 永久阻塞，多见于部分 Blackwell。本机模型与 `FULL_DECODE_ONLY` 相符，FlashInfer 版本未核对，调用栈未抓到。是否覆盖 DGX Spark（GB10）待确认。 |
| 3 | 现网参数未经对照 | `--quantization ascend` 在该镜像上是否为 NVFP4 的正确后端，未知。`cudagraph_mode=FULL_DECODE_ONLY`、prefix caching、`multistream_overlap_shared_expert` 是否提高挂死概率，尚未做单因子。`max-model-len 262144` 与 `max-num-seqs 12` 是否超出 Spark 上的推荐上限，待厂商给值。Running=14 高于 `--max-num-seqs 12`，计数口径未解释。 |
| 4 | 没有自动摘除 | 没有已验证的配置或补丁。若只探测 `/health`，假死期间仍可能被当成可用。关闭 CUDA Graph、更换量化参数、更换镜像 digest 的对照实验还没做。 |
| 5 | 扫描流量与挂死的关系未分开 | 非法 HTTP 与挂死同一时段出现。目前只记为暴露面。能否单独造成「无栈、generation=0、health 仍 200」，需要从 API Server / EngineCore 线程模型上判断。 |

**右栏 · 要求**

**请 NVIDIA 确认**

1. 该模型在 DGX Spark + `vllm/vllm-openai:qwen3.6` 上，是否已有 Silent Engine Hang、CUDA Graph 死锁或 `cuEventSynchronize` 挂起的已知问题；对应 issue 与修复版本；是否覆盖 GB10。
2. 不更换业务模型。给出 `--quantization` 的正确取值，以及 `cudagraph_mode` 的推荐值（含 `NONE` / `PIECEWISE` 是否可作规避，以及对吞吐的预期）。稳定性优先时，`multistream_overlap_shared_expert`、prefix caching、tool parser、reasoning parser 哪些应关闭。
3. 判断假死更像 scheduler 卡住、decode kernel 卡住，还是 HTTP 与 Engine 之间队列堵死。判断非法 HTTP 洪水能否单独造成该形态。
4. 给出假死当场的采集命令：`nvidia-smi`、Xid、`cuda-gdb` / `nsys` 挂哪个进程，vLLM 日志开到哪一级（`VLLM_LOGGING_LEVEL`、EngineCore）。
5. 若短期没有补丁：给出可上生产的上下文长度、并发、graph、量化配置；说明是否必须改用其他精度。

**验证顺序（低峰、可回滚；每组至少覆盖一次长于以往平均挂死时间的质控负载）**

| 优先级 | 要求 | 成功标准 |
|---|---|---|
| P0 | 保持现网命令，重启前先抓栈 | 拿到 Engine 阻塞点 |
| P0 | 核对镜像 digest、vLLM、CUDA、FlashInfer、驱动 | 对上已知问题，或排除版本 |
| P1 | 按厂商推荐改 `cudagraph_mode`，其余不变 | 同等负载不再挂死，或挂死显著后移 |
| P1 | 按厂商意见修正 `--quantization` | NVFP4 正确加载且长跑稳定 |
| P2 | 单因子关闭多流重叠与 prefix caching | 分清哪一个开关相关 |
| P2 | 仅内网/网关暴露，去掉扫描流量后再观察 | 确认挂死是否仍复现 |
| P3 | P1 无效时，评估厂商推荐镜像或非 NVFP4 路径 | 明确是否必须换权重或运行时 |

**重启前必须留下的材料**

挂死前 30 分钟完整日志（含最后一条非 0 的 generation）；`nvidia-smi`、`nvidia-smi -q`、`dmesg` 中的 Xid；EngineCore 与 API Server 的 Python / CUDA 栈；一条挂起请求的开始时间、prompt 长度、是否带 tool / reasoning；`docker inspect` 的 Image ID / digest 与驱动版本；启动命令全文与容器运行时长。

**现网兜底（与根因并行，不替代厂商修复）**

- 用真实短对话探测 `/v1/chat/completions`，超时即判不可用。
- 探测失败后先留现场日志，再做受控重启。

---

## 第 6 页 · 感谢页

**设计母题：** 与标题页同一版式。全页居中，只有致谢，无联系人、无二维码、无附录。

**标题：** 感谢
