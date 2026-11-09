# PPT 大纲：双 DGX Spark 推理运行时汇报

> **日期**：2026-11-09  
> **页数**：6 页内容（不含封面、致谢）  
> **结构**：三题各两页——迁移原因、迁移成绩与边界、Qwen3.6-NVFP4 静默挂死  
> **依据**：
>
> - `dgx-docs/汇报报告_双DGX-Spark_NIM到vLLM框架迁移分析_20261109.md`
> - `dgx-docs/汇报报告_Qwen3.6-NVFP4_SilentHang_英伟达技术沟通_20261109.md`

建议全稿统一口径：左上角固定「双 DGX Spark · 2026-11-09」，每页只留一句页眉结论，数字进表，口头补充放备注。

## 总结构

| 页 | 主题 | 设计母题 | 这一页只回答 |
|---|---|---|---|
| 1 | 一、框架怎么换 | 双栏对照：外壳相同，引擎不同 | NIM 与本项目 vLLM 各是什么 |
| 2 | 一、框架怎么换 | 单点故障：唯一 P0 | 为什么必须换，合格线是什么 |
| 3 | 二、换完到哪一步 | 验收成绩单 | 2026-08-26 证明了什么 |
| 4 | 二、换完到哪一步 | 边界三分 | 已解决、未解决、当时未封口 |
| 5 | 三、静默挂死 | 假死四特征 | 现场长什么样，和公开描述差在哪 |
| 6 | 三、静默挂死 | 厂商三问 + 行动阶梯 | 会上要确认什么，现网先做什么 |

---

## 第 1 页 · 两套运行时：同一权重，两套引擎

**主题：** 框架怎么换  
**设计母题：** 左右对照。左 NIM（官方基线，已停用），右 pinned vLLM（2026-08-26 后的生产运行时）。中间一条竖线只写三件相同的事：同一模型、同一权重 revision、都是 OpenAI 兼容接口。  
**页眉结论：** 换的是推理引擎和交付外壳，模型权重没有换。

**页题：** NIM 1.11.0-variant 与双 Spark 固定 vLLM

**中间共有项（竖条，三条即可）**

- 模型：`deepseek-ai/DeepSeek-V4-Flash-0731`，约 155G / FP8
- 权重 revision：`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`
- 硬件：2× DGX Spark GB10（SM121），TP=2，ConnectX-7 / RoCE

**左栏 · NIM，官方基线，现已停用**

| 项 | 内容 |
|---|---|
| 镜像 | `nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant` |
| 角色 | NGC 官方容器；容器自己拉权重、拉起双机 rank、对外 `:8000/v1` |
| 进程 | Node0 `deepseek-v4-rank0`，Node1 `deepseek-v4-rank1` |
| 引擎 | 容器内是 **SGLang**，prefill 走 sparse MLA；手册首次请求记为 SGLang autotuning |
| 短上下文 | 约 24K 可通过；1K / 8K decode 为 19.6 / 21.9 tok/s |
| 内存 | 每节点约 113 / 121 GiB，统一内存基本占满 |

**右栏 · 本项目 vLLM，固定构建，现网运行时**

| 项 | 内容 |
|---|---|
| 镜像 | `ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731` |
| digest | `sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b`；本地 tag `pinned-676a1c89`（arm64，24.8GB） |
| 配方 | `8a161c09f1269ee8ea18ba324b4682d0712f4e4a` |
| 钉死组合 | CUDA 13.2 · PyTorch 2.13.0+cu132 · vLLM 0.26.1rc0（result tree `a47a2f80278ef728f143ad851b08ac5b5001a0ac`）· 补丁 dspark.r16 · FlashInfer `1ac6942776b383c6b03c7a5805a22e72a3e3349f` · NCCL 2.30.4 |
| 并行与解码 | TP=2，DCP=1；DSpark 推测解码 spec=5 |
| 服务 | `:18090`，模型名 `deepseek-v4-flash-0731` |
| 短上下文 | 1K / 8K decode 为 47.3 / 43.1 tok/s；内存同样占满 |

**页脚一条：** 两套 compose、两个端口，不允许同时常驻。切换时先停 NIM、保留容器，再起 vLLM。上游 `vllm latest` / `flashinfer latest` 不采用：GB10 上 CUDA、FlashInfer、vLLM 提交必须一起匹配，否则 40K 崩溃无法对照。

**备注：** 点明 NIM 在这里是交付外壳，真正算 prefill 的是里面的 SGLang sparse MLA kernel。

---

## 第 2 页 · 迁移只有一个理由：40K 把调度进程打崩

**主题：** 框架怎么换  
**设计母题：** 上半是一条上下文刻度（24K 可用 → 32K 以上崩溃 → 40K 实锤），下半是当时的三条合格线。整页只有一个红点：`sparse_mla_sm120_prefill.cu:67`。  
**页眉结论：** 换框架只为消掉这一处 P0，不为换包，也不为先追最高吞吐。

**页题：** GB10 上 NIM 的长上下文崩溃

**刻度（从左到右，三档）**

| 档 | 现场 | 判定 |
|---|---|---|
| 约 24K | 实际 prompt 可以通过；1K 19.6 tok/s，8K 21.9 tok/s | 短上下文可用 |
| 大于 32K | 手册：触发同一 CUDA crash | 进入不可用区 |
| 约 40K | 进程 abort | P0 实锤 |

**崩溃原文（代码块，原样放）**

```text
约 40K prompt → CUDA crash
CUDA error at sparse_mla_sm120_prefill.cu:67: operation not permitted
Fatal Python error: Aborted
scheduler_0 exit -6
```

**三句机制（放在代码块右侧，短句）**

- 硬件是 GB10 / SM121，崩溃点落在 SM120 的 sparse MLA prefill。
- 引擎 kernel 把调度进程打崩；调 KV 长度或并发绕不开。
- 与裸 SGLang 的 Sparse MLA 同类。方案写明：若 vLLM 仍失败，换另一套 pinned vLLM，退路排除裸 SGLang。

**当时合格线（页底三块等宽卡片）**

1. 40K 通过
2. 64K 通过
3. 0 次 CUDA crash

同时要求：tool calling、reasoning、prefix cache，以及 8 小时 soak。128K 放到 64K 与 8 小时都通过之后的 canary。社区材料里的 262K 请求上限、131K 冷 prefill、1M 配置长度，不作为本机保证。

---

## 第 3 页 · 2026-08-26：40K 崩溃消失，64K 已验收

**主题：** 换完到哪一步  
**设计母题：** 成绩单。上排四个结果徽章，下排一张上下文验收表，右下角一块吞吐对照。绿色只用于「3/3」和「0 次 CUDA crash」。  
**页眉结论：** 推测解码开着通过验收，没有走到「先关 spec 再测」的退路。

**页题：** 迁移验收：64K 稳定，短上下文 decode 约 2 倍

**四个徽章**

| 徽章 | 数字 |
|---|---|
| 40K（原崩溃点） | 3/3 |
| 真 64K | 61509 tokens，3/3 |
| 冷 prefill / 前缀命中 | 16.7s / TTFT 0.7s |
| CUDA crash | 0 |

**上下文矩阵**

| 长度 | 结果 |
|---|---|
| 8K / 16K / 24K / 32K | 各 3/3 |
| 40K | 3/3 |
| 48K / 64K | 各 3/3 |
| tool calling、reasoning | 通过 |

编程场景关闭 thinking，避免小 `max_tokens` 被思考过程耗尽。

**吞吐对照（NIM → 本项目 vLLM）**

| 项 | NIM | vLLM | 倍数 |
|---|---:|---:|---:|
| 1K decode | 19.6 tok/s | 47.3 tok/s | 2.4 |
| 8K decode | 21.9 tok/s | 43.1 tok/s | 2.0 |
| TTFT | 约 0.3～0.4s | 约 0.3～0.4s | 持平 |

吞吐提升对应镜像内打开的 DSpark 推测解码（spec=5）。方案里的退路是：若 40K 仍崩，先关推测解码再测 24K / 40K / 64K；关了才通过，就用稳定模式生产。验收时这条退路没有走到。

**页脚：** 128K 不作为当时的生产资格线。

---

## 第 4 页 · 64K 以下已换路；靠近 128K 失败点变了

**主题：** 换完到哪一步  
**设计母题：** 三列状态。已解决 / 未解决 / 当时未封口。中间用一条时间线串起 08-26、09-02、09-10，强调后两次崩溃的文件和退出码都变了。  
**页眉结论：** 迁移消掉了 SGLang 在约 40K 的 abort；靠近 128K 仍是这版 pinned vLLM 的稳定性问题。

**页题：** 成绩边界：失败从 sparse MLA abort 换成 cuBLAS / 采样超时

**时间线**

| 日期 | 现象 | 和原 P0 的关系 |
|---|---|---|
| 2026-08-26 | 40K / 64K 验收通过 | 原 P0 消失 |
| 2026-09-02 | KV 约 12.6 万 token（靠近 131072）时，`cublasGemmEx`（BF16 tensor op）返回 `CUBLAS_STATUS_INTERNAL_ERROR`，EngineDeadError，进程 exit 0。崩溃前有 warmup 未覆盖的 Triton kernel 在线编译 | 同一文件没有复发；这版 vLLM 在接近 128K 的 prefill 上另有计算错误 |
| 2026-09-10 | 大请求触发 `sample_tokens` RPC 超时，同样 EngineDeadError、exit 0。单发约 113K 后来可以成功，属于偶发 | 仍是 `pinned-676a1c89` 的大 prefill 稳定性问题 |

**三列（本页主体）**

| 已解决 | 未解决 | 当时未封口 |
|---|---|---|
| SGLang sparse MLA 在约 40K 把进程 abort | `pinned-676a1c89`（vLLM 0.26.1rc0 + dspark.r16）靠近 128K 时，cuBLAS 或 `sample_tokens` 超时导致退出 | 30 分钟 / 2 小时 / 8 小时 soak |
| 64K 作为已验收上下文 | 生产用 **125K 输入硬顶** 挡住该区间 | 「64K + 8 小时通过之后才允许 128K canary」 |
| 短上下文 decode 约 2 倍，TTFT 持平 | 125K 硬顶是网关限制 | 后来的 128K 故障发生在资格线还没封口时 |

**页脚两条**

- 64K 以下走已验收的 vLLM 路径。
- 裸 SGLang 明确排除，不能当作退路。

---

## 第 5 页 · Qwen3.6-NVFP4：质控长跑后静默挂死

**主题：** 静默挂死（对象：NVIDIA 工程师技术沟通；状态：待厂商确认根因与规避）  
**设计母题：** 上半四格现象，下半「正常段 → 16:03:07 归零」对照。健康检查 200 用警示色，表示它不能代表推理可用。  
**页眉结论：** 生成吞吐归零，进程还在，日志没有异常栈；重启可恢复，随运行时间再次出现。

**页题：** WiNBOT 病历质控上的 Silent Engine Hang 形态

**对象条（页题下一条细字）**

- 平台：DGX Spark 工作站
- 模型：`Qwen3.6-35B-A3B-NVFP4`（NVFP4 量化 MoE）
- 镜像：`vllm/vllm-openai:qwen3.6`，`vllm serve`，`0.0.0.0:8001`，对外名 `qwen3.6`
- 业务：WiNBOT 病历质控，山西晋城试点，心内科、骨科
- 当前恢复：重启模型服务

**四格现象**

| 请求 | 引擎统计 | 日志与进程 | 健康检查 |
|---|---|---|---|
| `POST /v1/chat/completions` 挂起，客户端长时间无返回 | generation throughput 降为 0；Running / Waiting 仍在 | 没有明确异常栈；进程往往仍在，不是干净退出 | `/health`、`/v1/models` 仍可能 HTTP 200 |

**负载（说明这是长稳问题，短请求可以成功）**

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
| Running / Waiting | Running 14（高于 `--max-num-seqs 12`，计数口径待厂商解释）；Waiting 0～8 | Running=14，Waiting=8，请求仍在队列 |
| GPU KV cache | 约 2.4%～2.5% | 同样很低 |
| Prefix cache hit | 约 1.9% | — |

KV 占用很低，现场不像 KV 耗尽或显存打满。

**和公开 Silent Engine Hang 的对齐**

- `/health` 仍 200、chat 挂起、日志无错误栈：现场符合。
- 模型是 NVFP4 MoE，启动使用 `cudagraph_mode=FULL_DECODE_ONLY`：路径相符；FlashInfer 版本尚未核对。
- 公开分析怀疑 kernel 死循环或 `cuEventSynchronize` 永久阻塞，且多见于部分 Blackwell 上的 NVFP4 MoE。
- 本机尚未抓到调用栈，该机制不能当作已证实结论；是否覆盖 DGX Spark（GB10）需厂商确认。

**备注：** 同一时段有大量 `Invalid HTTP request received`，以及 `/phpMyAdmin/`、`/HNAP1`、`/solr/admin/` 等 404。当前视为并发暴露面，不作为已证实根因。

---

## 第 6 页 · 请厂商确认的三件事，以及现网并行动作

**主题：** 静默挂死  
**设计母题：** 左 60% 是「会上三问」，右 40% 是参数核对清单；底部一条 P0–P3 阶梯，最底一行是业务兜底。根因实验尚未做，不要画成「已排除」。  
**页眉结论：** 补丁明确之前，现网只能重启恢复；本次要的是可执行参数规避和取证步骤。

**页题：** NVIDIA 技术沟通：根因、参数、取证

**会上三问（左栏）**

1. **是不是已知缺陷。** `Qwen3.6-35B-A3B-NVFP4` 在 DGX Spark + `vllm/vllm-openai:qwen3.6` 上，是否已有 Silent Engine Hang、CUDA Graph 死锁或 `cuEventSynchronize` 挂起？对应 issue 和修复版本是什么？是否覆盖 GB10？
2. **不换业务模型时怎么配。** `--quantization` 的正确取值是什么，`ascend` 在该镜像上会被忽略、报错，还是走到错误 kernel？`cudagraph_mode` 推荐 `NONE`、`PIECEWISE` 还是维持 `FULL_DECODE_ONLY`，吞吐预期如何？`multistream_overlap_shared_expert`、prefix caching、tool parser、reasoning parser，稳定性优先时哪些应关？
3. **假死当场抓什么。** generation 为 0 但 Running/Waiting 仍在，更像 scheduler 卡住、decode kernel 卡住，还是 HTTP 与 EngineCore 之间队列堵死？非法 HTTP 洪水能否单独造成「无栈、generation=0、health 仍 200」？`nvidia-smi`、Xid、`cuda-gdb` / `nsys` 挂哪个进程，vLLM 日志开到哪一级？

**请厂商核对的现网参数（右栏）**

```text
--quantization ascend
--max-model-len 262144
--max-num-seqs 12
--max-num-batched-tokens 4096
--gpu-memory-utilization 0.90
--enable-prefix-caching
--compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}'
--additional-config '{"enable_cpu_binding":true, "multistream_overlap_shared_expert": true}'
```

另附：`--enable-auto-tool-choice`，`--tool-call-parser qwen3_coder`，`--reasoning-parser qwen3`，thinking 关闭。镜像 digest、vLLM / CUDA / FlashInfer、驱动版本本次材料未固定，列为会前补齐项。

**验证阶梯（低峰、可回滚；每组至少覆盖一次长于以往平均挂死时间的质控负载）**

| 优先级 | 实验 | 成功标准 |
|---|---|---|
| P0 | 现网命令不变，重启前按取证清单抓栈 | 拿到 Engine 阻塞点 |
| P0 | 核对镜像 digest、vLLM、CUDA、FlashInfer、驱动 | 对上已知问题，或排除版本 |
| P1 | `cudagraph_mode` 改为关闭或厂商推荐值 | 同等负载不再挂死，或挂死显著后移 |
| P1 | 按厂商意见修正 `--quantization` | NVFP4 正确加载且长跑稳定 |
| P2 | 单因子关闭多流重叠、prefix caching | 分清哪一个开关相关 |
| P2 | 仅内网/网关暴露，去掉扫描流量后再观察 | 确认挂死是否仍复现 |
| P3 | P1 无效时，评估厂商推荐镜像或非 NVFP4 路径 | 明确是否必须换权重或运行时 |

**取证清单（重启前，可作附录小字或口头备查）**

1. 挂死前 30 分钟完整服务日志（含最后一条非 0 的 generation 统计）。
2. `nvidia-smi`、`nvidia-smi -q`、`dmesg` 中的 Xid / GPU fallen off。
3. EngineCore 与 API Server 的 Python / CUDA 调用栈。
4. 当时一条挂起的 chat 请求：开始时间、prompt 长度、是否带 tool / reasoning。
5. 镜像 `docker inspect` 的 Image ID / digest，以及驱动版本。
6. 启动命令全文与容器运行时长。

**现网兜底（与根因并行，不替代厂商修复）**

- 用真实短对话探测 `/v1/chat/completions`，超时即判不可用。
- 探测失败后先留现场日志，再做受控重启。
- 尚无已验证的配置或补丁；只探 `/health` 会在假死期间仍看到 200。
