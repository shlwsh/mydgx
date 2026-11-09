# DgxSpark应用沟通交流｜PPT 逐页大纲

**汇报日期** 2026-11-09  
**沟通对象** NVIDIA 工程师  
**页数** 6 页。框架迁移和静默挂死各占两页，先展示运行表现，再讨论故障点和需要确认的事项。

制作时将「屏幕内容」排入幻灯片，「讲稿备注」供讲解与答疑。封面和结束页采用相同的居中版式。

## 第 1 页 · 标题页

**屏幕内容**

DgxSpark应用沟通交流

## 第 2 页 · DeepSeek-V4-Flash 迁移前后的运行表现

**屏幕内容**

双 DGX Spark（GB10 / SM121，TP=2）使用同一版 DeepSeek-V4-Flash-0731 FP8 权重。两套服务均提供 OpenAI 兼容接口。

| 现场指标 | NIM 1.11.0-variant | 固定版 vLLM `pinned-676a1c89` |
|---|---|---|
| 上下文 | 约 24K 可用，约 40K 触发 CUDA 崩溃 | 40K、64K 均通过 3/3，靠近 128K 曾异常退出 |
| 1K / 8K 解码吞吐 | 19.6 / 21.9 tok/s | 47.3 / 43.1 tok/s（约 2.4 / 2.0 倍） |
| 短上下文首 token 延迟 | 约 0.3～0.4 秒 | 约 0.3～0.4 秒 |

2026-08-26 验收覆盖 8K～64K，工具调用、推理和前缀缓存均通过验证。

**版式** 上方一行共同测试条件，下方一张对照表。突出 40K 的迁移结果和 64K 的验收范围。

**讲稿备注**

- 双机使用 ConnectX-7 / RoCE。模型约 155G，权重 revision 为 `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`。NIM 是 NVIDIA 官方容器，该 variant 内部使用 SGLang。vLLM 镜像由 liquidgravityai 为双 Spark 固定打包。
- 8K、16K、24K、32K、40K、48K、64K 各正式测试 3 次，全部通过。实际输入 61,509 token 的长请求也通过 3/3，冷 prefill 16.7 秒，共享前缀命中后首 token 延迟 0.7 秒。短上下文解码数据来自开启 `spec=5` 的整套验收配置。
- 两套运行时均大量占用统一内存，切换时先停止 NIM 再启动 vLLM。NIM 容器保留。

**材料** [框架迁移分析 §1–3](汇报报告_双DGX-Spark_NIM到vLLM框架迁移分析_20261109.md) · [迁移验收记录 §4–6](../deepseek-v4-flash-dual-dgx/reports/vllm-migration-report.md)

## 第 3 页 · 40K 故障已绕开，128K 仍待验证

**屏幕内容**

| 故障点与状态 | 处置与待确认事项 |
|---|---|
| **NIM 约 40K** · SGLang 的 `sparse_mla_sm120_prefill.cu:67` 报 CUDA 错误，调度进程以 `-6` 退出 | 生产已切至固定版 vLLM。请 NVIDIA 确认 GB10 上该 NIM 路径的支持范围，以及是否有修复版本。 |
| **vLLM 大请求退出** · 9 月 2 日接近 128K 时出现 cuBLAS 内部错误，9 月 10 日大请求出现 `sample_tokens` RPC 超时。两次均报 `EngineDeadError`，进程退出码为 `0` | 保留 125K 网关输入硬顶。请 NVIDIA 建议应补采的日志，以及可用于对照的运行时版本。 |
| **128K 生产资格待验证** · 64K 已通过功能验收，长稳测试尚未完成 | 在 64K 条件下依次完成 30 分钟、2 小时、8 小时长稳测试，全部通过后才开展 128K 小流量验证。 |

**版式** 三行对照。左侧标注「已换路」「仍待定位」「待验证」，右侧放对应行动。

**讲稿备注**

- 固定构建组合为 vLLM 0.26.1rc0、dspark.r16、CUDA 13.2、PyTorch 2.13.0+cu132、FlashInfer `1ac69427…`、NCCL 2.30.4、TP=2 / DCP=1。镜像 digest 和配方 commit 见迁移分析 §1.2。回归测试沿用验收时的 `spec=5`。若 40K 再次崩溃，先关闭推测解码，复测 24K、40K 和 64K。若需换构建，长上下文路径仍选经验证的固定版 vLLM。
- 9 月 2 日故障时 KV 约 12.6 万 token，`cublasGemmEx` 返回 `CUBLAS_STATUS_INTERNAL_ERROR`。故障前还记录到 warmup 未覆盖的 Triton kernel 在线编译，其与错误的关系待查。9 月 10 日的 RPC 超时后，单发约 113K 曾成功。125K 是网关的**输入**限制，64K 是已验收的上下文档位，128K 是待验证目标。

**材料** [框架迁移分析 §2–4](汇报报告_双DGX-Spark_NIM到vLLM框架迁移分析_20261109.md) · [迁移验收记录 §9](../deepseek-v4-flash-dual-dgx/reports/vllm-migration-report.md)

## 第 4 页 · Qwen3.6 质控长跑后停止生成

**屏幕内容**

WiNBOT 病历质控需要多日连续运行。试点使用 DGX Spark、`Qwen3.6-35B-A3B-NVFP4` 和 `vllm/vllm-openai:qwen3.6` 镜像。

| 观测项 | 正常段 | 约 16:03:07 |
|---|---|---|
| 生成吞吐 | 约 67～284 tokens/s | **0.0 tokens/s** |
| Running / Waiting | 14 / 0～8 | **14 / 8** |
| GPU KV 缓存占用 | 约 2.4%～2.5% | 2.5% |

- `/v1/chat/completions` 长时间无返回，`/health` 和 `/v1/models` 仍可能返回 HTTP 200。
- 进程往往仍在，日志没有明确异常栈。重启后恢复，持续运行后可复发。

**版式** 左侧用现场日志标出归零行，右侧保留三项对照和两条服务表现。截图仅保留必要的引擎统计行。 [现场日志截图](assets/fig1_vllm_fake_hang_log.png)

**讲稿备注**

- 试点位于山西晋城，覆盖心内科与骨科。2026-10-29 至 11-05，在院病历增加 1,065 份，归档病历增加 537 份。短请求可以成功，挂死在长时间运行后出现。
- 同期有高频非法 HTTP 和非推理路径 404。它们与停转的因果关系待确认。日志的 Running=14 高于参考启动参数 `--max-num-seqs 12`，计数口径也待核对。

**材料** [静默挂死技术沟通报告 §2–6](汇报报告_Qwen3.6-NVFP4_SilentHang_英伟达技术沟通_20261109.md)

## 第 5 页 · NVFP4 挂死的定位与规避

**屏幕内容**

| 待定位的故障点 | 请 NVIDIA 确认 |
|---|---|
| **阻塞位置** · 请求挂起、生成归零，尚缺调用栈 | 优先排查调度器、decode kernel、CUDA 同步点还是 API Server 与 EngineCore 间的队列？请给出挂死时应采集的进程、命令和日志级别。 |
| **量化与 CUDA Graph** · 参考配置为 `--quantization ascend`、`FULL_DECODE_ONLY` | 该权重在 GB10 上的正确量化参数是什么？`NONE` 或 `PIECEWISE` 能否规避，预计影响多少吞吐？ |
| **适用版本与生产配置** · 已记录镜像 tag，digest、组件版本和驱动待核对 | 请提供适用 GB10 的已知 issue、修复镜像或补丁，并给出保留现有业务模型时的推荐上下文和并发。 |

**复现时** 真实短对话探测超时 → 保存现场并抓栈 → 受控重启。  
**验证时** 先锁定版本与基线，再按厂商意见逐项测试 CUDA Graph、量化和其他开关。

**版式** 三行问题与答复对照。页脚分两行放复现处置和验证顺序。

**讲稿备注**

- NVFP4 MoE 与 FlashInfer、CUDA Graph 的组合是排查方向。本机尚未采到 kernel 死循环或 `cuEventSynchronize` 阻塞的调用栈。向厂商补问高频非法 HTTP 能否独立造成同样的挂起，以及 Running/Waiting 的统计口径。
- 首轮保留现网参数并记录镜像 digest、vLLM / CUDA / FlashInfer 及驱动版本。重启前保存故障前 30 分钟日志（含最后一条非零吞吐）、GPU 状态和 Xid、EngineCore 与 API Server 的 Python / CUDA 栈、挂起请求的时间与输入长度、启动命令及容器运行时长。请厂商明确 `cuda-gdb` / `nsys` 的采集对象和 `VLLM_LOGGING_LEVEL` 设置。
- 在低峰可回滚窗口逐项对照。先测 Graph 模式，再核对量化后端，随后分别测试 shared expert 多流重叠和前缀缓存，另做仅经内网或网关的复现对照。每组覆盖同等业务负载，运行时间超过历史平均挂死时间，记录是否复发、首次挂死时间及吞吐。现网参考上限为 `max-model-len=262144`、`max-num-seqs=12`、`max-num-batched-tokens=4096`，需请厂商给出 Spark 上的建议值。

**材料** [静默挂死技术沟通报告 §3、§7–9](汇报报告_Qwen3.6-NVFP4_SilentHang_英伟达技术沟通_20261109.md)

## 第 6 页 · 感谢页

**屏幕内容**

感谢

---

**编排参考** [原六页 PPT 大纲](PPT大纲_双DGX-Spark_框架迁移与NVFP4静默挂死_20261109.md)
