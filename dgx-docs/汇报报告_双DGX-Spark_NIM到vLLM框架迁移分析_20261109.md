# 分析报告：双 DGX Spark 上 NIM → vLLM 的框架迁移

> **日期**：2026-11-09  
> **模型**：`deepseek-ai/DeepSeek-V4-Flash-0731`（约 155G / FP8）  
> **硬件**：2× DGX Spark GB10（SM121），TP=2，ConnectX-7 / RoCE  
> **依据**：`docs/项目说明手册_20260826-2241.md`、`deepseek-v4-flash-dual-dgx/双DGX-Spark_DeepSeek-V4-Flash_NIM迁移vLLM方案_Cursor可执行_20260826.md`、`deepseek-v4-flash-dual-dgx/reports/vllm-migration-report.md`  
> **范围**：只分析推理框架为什么换、换成什么、换完解决了什么。网络、代理、端口、主机搬迁不在本文。  
> **与原纪要的关系**：原报告 `汇报报告_双DGX-Spark_推理框架迁移与故障纪要_20261109.md` 保持不动。本文是另存的框架迁移分析版。

---

## 1. 两个框架分别是什么

两边都对外提供 OpenAI 兼容的 chat API，模型权重用的是同一个 revision（`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`）。差别在**谁打包、里面的推理引擎是哪一套**。

### 1.1 NIM：NVIDIA 的官方推理容器

NIM（NVIDIA Inference Microservices）是 NVIDIA 把模型、运行时和 OpenAI 兼容接口打成的 NGC 容器。本项目用的是：

```text
nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant
```

它是这条模型在双机上的**官方基线**。容器自己完成权重拉取、双机 rank 启动和 `:8000/v1` 服务。Node0 容器 `deepseek-v4-rank0`，Node1 容器 `deepseek-v4-rank1`。

这版 variant 里面的执行引擎是 **SGLang**，不是 vLLM，也不是 TensorRT-LLM。依据有两处：

- 项目手册把 NIM 的首次请求写成 SGLang autotuning。
- 长上下文崩溃落在 `sparse_mla_sm120_prefill.cu`。迁移方案把这个 P0 和「裸 SGLang 的 Sparse MLA」写成同一类，并写明：若 vLLM 仍失败，可以换另一套 pinned vLLM，**不要退回裸 SGLang**。

所以 NIM 在这里的角色是 NVIDIA 的交付外壳；真正算 prefill 的是里面的 SGLang sparse MLA kernel。

### 1.2 vLLM：开源推理引擎，本项目用的是双 Spark 固定构建

vLLM 是开源的大模型推理框架（分页 KV、continuous batching、OpenAI 兼容服务）。本项目**没有**用上游浮动版本：

```text
不采用：pip install -U vllm / vllm latest / flashinfer latest
```

采用的是针对 **2× DGX Spark、GB10、SM121** 打好的固定镜像，来源是第三方仓库 liquidgravityai，不是 NGC 上的 NIM：

```text
ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731
@sha256:676a1c896510f31b0bb063e16e133ac24100a4d0f0bd9c70128ecde69ef17d0b
本地 tag：pinned-676a1c89（arm64，24.8GB）
配方仓库：github.com/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731
配方 commit：8a161c09f1269ee8ea18ba324b4682d0712f4e4a
```

镜像里钉死的组合：

| 组件 | 版本 |
|---|---|
| 架构 | ARM64 / GB10 SM12.1 |
| CUDA | 13.2 |
| PyTorch | 2.13.0+cu132 |
| vLLM | 0.26.1rc0，result tree `a47a2f80278ef728f143ad851b08ac5b5001a0ac` |
| 补丁 | dspark.r16 |
| FlashInfer | `1ac6942776b383c6b03c7a5805a22e72a3e3349f` |
| NCCL | 2.30.4 + CUDA 13.2 |
| 并行 | TP=2，DCP=1 |
| 额外解码 | DSpark speculative decoding，spec=5 |

服务端口是 `:18090`。模型名在 vLLM 侧是 `deepseek-v4-flash-0731`。

固定 digest 的原因：GB10 上 CUDA、FlashInfer、vLLM 提交必须一起匹配。`latest` 对不上这组硬件时，长上下文行为无法复现，也不能和 NIM 的 40K 崩溃做对照。

### 1.3 对照

| | NIM 1.11.0-variant | 本项目的 vLLM |
|---|---|---|
| 来源 | NVIDIA NGC 官方容器 | 开源 vLLM，由 liquidgravityai 按双 Spark 固定打包 |
| 推理引擎 | SGLang（sparse MLA prefill） | vLLM + FlashInfer + DSpark 推测解码 |
| 在本项目中的角色 | 先上线的官方基线，现已停用，保留回滚记录 | 2026-08-26 验收后的生产运行时 |
| 上下文 | 约 24K 可通过；约 40K / 大于 32K 崩溃 | 验收到 64K；128K 不作为当时的生产资格线 |
| 1K / 8K decode | 19.6 / 21.9 tok/s | 47.3 / 43.1 tok/s |
| 内存 | 每节点约 113/121 GiB | 同样占满统一内存 |

两套运行时各有自己的 compose，端口不同，但**不允许同时常驻**。NIM 已经把每台机器的统一内存用到约 113GiB，再拉起 vLLM 没有余量。切换时先停 NIM、不删容器，再起 vLLM。

---

## 2. 为什么必须换

换框架只为解决一个 P0，不是为了换包或先追最高吞吐。

NIM 暖机后短上下文是可用的：1K 约 19.6 tok/s，8K 约 21.9 tok/s，约 24K 的实际 prompt 可以通过。再往上：

```text
约 40K prompt → CUDA crash
CUDA error at sparse_mla_sm120_prefill.cu:67: operation not permitted
Fatal Python error: Aborted
scheduler_0 exit -6
```

手册的表述是：大于 32K 即触发该文件中的 CUDA crash。硬件是 GB10 / SM121，崩溃点在 SM120 的 sparse MLA prefill。这是引擎 kernel 把调度进程打崩，调 KV 长度或并发绕不开。

当时的合格线因此只有三条：

- 40K 通过
- 64K 通过
- 0 次 CUDA crash

社区材料里这条 vLLM 双 Spark 路线写过 262K 请求上限，也有别的配方做过 131K 冷 prefill 和 1M 配置长度。本项目没有把这些数字当成自己机器上的保证。第一阶段只要求 64K、tool calling、reasoning、prefix cache，以及 8 小时 soak。128K 放到后面的 canary。

---

## 3. 迁移后引擎侧发生了什么

2026-08-26 的验收结论是：**40K 崩溃消失，64K 稳定，短上下文吞吐约 2 倍。**

| 检查 | 结果 |
|---|---|
| 8K / 16K / 24K / 32K | 各 3/3 |
| 40K（NIM 的崩溃点） | 3/3 |
| 48K、64K | 各 3/3 |
| 真 64K（61509 tokens） | 3/3。冷 prefill 16.7s；共享前缀命中后 TTFT 0.7s |
| tool calling、reasoning | 通过。编程场景用 thinking 关闭，避免小 `max_tokens` 被思考过程耗尽 |
| decode | 1K 2.4 倍，8K 2.0 倍；TTFT 与 NIM 持平（约 0.3～0.4s） |

吞吐提升对应镜像里打开的 DSpark 推测解码（spec=5）。迁移方案写了退路：若 40K 仍崩，先关推测解码再测 24K/40K/64K；关了才通过，就用稳定模式生产。实际验收时推测解码开着并通过了，没有走到这条退路。

64K 之后，框架问题没有结束，只是失败点换了。

| 时间 | 仍属推理引擎的问题 | 和 NIM 那个 P0 的关系 |
|---|---|---|
| 2026-09-02 | 请求把 KV 打到约 12.6 万 token（靠近 131072）时，`cublasGemmEx`（BF16 tensor op）返回 `CUBLAS_STATUS_INTERNAL_ERROR`，EngineDeadError，进程 exit 0。崩溃前有一个 warmup 没覆盖的 Triton kernel 在线编译 | 不是 `sparse_mla_sm120_prefill.cu` 的原样复发。是这版 vLLM 在接近 128K 的 prefill 上另有计算错误 |
| 2026-09-10 | 大请求触发 `sample_tokens` RPC 超时，同样 EngineDeadError、exit 0。单发约 113K 后来可以成功，属于偶发 | 仍是该 pinned build 的大 prefill 稳定性问题，不是 NIM 的 SM120 kernel |

因此迁移的成绩边界是：

- **已解决**：SGLang sparse MLA 在约 40K 把进程 abort 的问题；64K 可作为已验收上下文。
- **未解决**：`pinned-676a1c89`（vLLM 0.26.1rc0 + dspark.r16）在靠近 128K 时仍会因 cuBLAS 或采样 RPC 超时退出。生产侧用 125K 输入硬顶挡住这个区间，这是网关限制，不是引擎修复。
- **当时就没做完**：30 分钟 / 2 小时 / 8 小时 soak，以及「64K 加 8 小时通过之后才允许 128K canary」。后面的 128K 故障发生在这条资格线还没封口的时候。

---

## 4. 结论

1. **NIM** 是 NVIDIA 在 NGC 上发布的官方推理容器。本项目的 `1.11.0-variant` 用 SGLang 做 DeepSeek 的 sparse MLA prefill。它能完成双机短上下文服务，但在 GB10 上大约 40K 会在 `sparse_mla_sm120_prefill.cu:67` 崩溃。
2. **vLLM** 是开源推理框架。本项目跑的不是上游 latest，而是 liquidgravityai 为 2× DGX Spark 钉死的一版：vLLM 0.26.1rc0、FlashInfer、CUDA 13.2、DSpark 推测解码。
3. 迁移原因只有这一条 P0。换完之后 40K/64K 验收通过，短上下文 decode 约 2 倍，TTFT 持平。
4. 换引擎没有把长上下文风险清掉。64K 以下换成了 vLLM 路径并已验收；靠近 128K 时，失败从 SGLang 的 sparse MLA abort 变成了这版 vLLM 的 cuBLAS / `sample_tokens` 超时。裸 SGLang 被明确排除，不能当作退路。

---

## 5. 材料

| 文档 | 对应内容 |
|---|---|
| `docs/项目说明手册_20260826-2241.md` §3.3、§8、§9 | NIM 与 vLLM 的定位、吞吐、迁移摘要 |
| `deepseek-v4-flash-dual-dgx/双DGX-Spark_DeepSeek-V4-Flash_NIM迁移vLLM方案_Cursor可执行_20260826.md` §1～§2、§29 | P0 原文、为什么不用 latest、禁止退回裸 SGLang |
| `deepseek-v4-flash-dual-dgx/reports/vllm-migration-report.md` | 2026-08-26 验收数字 |
| `deepseek-v4-flash-dual-dgx/reports/incident-20260902-vllm-roce-gid.md` §5 | 迁移后靠近 128K 的 cuBLAS 崩溃 |
| `docs/故障处理记录_20260910_vLLM推理不稳定修复.md` | 同镜像上大请求 `sample_tokens` 超时 |
