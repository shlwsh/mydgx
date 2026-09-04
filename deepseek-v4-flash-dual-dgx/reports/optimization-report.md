# DeepSeek-V4-Flash-0731 优化报告（Phase A + B）

> 时间：2026-08-26 09:40 +08:00
> 基准：Golden Baseline 快照于 `/opt/deepseek-v4/ops/current/`（双机）
> 服务状态：在线（API 200）

---

## Phase A 完成项（零风险，未改任何 NIM 参数）

### 1. Golden Baseline 快照 ✅
- `/opt/deepseek-v4/ops/{current,configs,scripts,benchmark,logs,reports}` 双机建立
- 已保存：rank0/rank1 docker inspect、process 列表、env、netplan、docker info

### 2. Warm Baseline（streaming 实测，warmup×2 后正式 N 次 median/p95）

| Case | 输入/输出 tokens | TTFT (median) | Decode tps | TPOT | E2E | 成功率 |
|---|---|---|---|---|---|---|
| A | ~577 / 256 | **0.26 s** | **19.6** | 50.9 ms | 13.2 s | 3/3 |
| B | ~4591 / 512 | **0.40 s** | **21.9** | 45.7 ms | 16.1 s | 5/5 |
| C | ~18K / 231* | **0.41 s** | **22.4** | 44.7 ms | 10.7 s | 3/3 |
| 并发 2 (8K/512) | — | 0.45 / 0.84 s | **27.7 聚合** | — | wall 32.1s | — |
| 并发 4 (8K/512) | — | 0.47~1.30 s | （见 warm-conc4.json） | — | — | — |

\* C case 的 tokenizer 计数与字符估算有偏差（18353 实际 vs 目标 32K），不影响 TTFT/tps 结论。

对比 cold baseline：decode 稳定在 **19–22 tps**（此前 6.5–14.4），TPOT ~45ms，交互体验显著更好。

### 3. Radix/Prefix Cache 验证 ✅
- SGLang `--disable-radix-cache` 未设置；无 DISABLE_RADIX_CACHE 环境变量 → radix cache 默认启用
- **24K 共享前缀实验：首次 TTFT 18.14s → 第二次 0.39s（改善 97.8%）**
- 远超方案目标（30%，理想 50%+）。编程场景（固定 system prompt+项目上下文）收益巨大
- **实践要求：prompt 稳定前缀顺序不可变**（system→项目背景→规则→历史→任务）

### 4. 推理模式验证 ✅（NIM API 层面）
| 模式 | 参数 | 实测 |
|---|---|---|
| Fast（默认） | `chat_template_kwargs.thinking=false` | 0.29s，无 reasoning ✅ |
| Think | `thinking=true` | reasoning_content 产生 ✅ |
| Max | + `reasoning_effort:max` | 被接受 ✅ |
| tool_choice=auto | tools 正常返回 ✅ 无 400 |

### 5. 运维工具 ✅
- `/opt/deepseek-v4/ops/scripts/status.sh` — 一键集群状态
- `/opt/deepseek-v4/ops/scripts/warmup.sh` — 启动后自动暖机（等 API→1K×2→4K×1）
- systemd：`deepseek-v4-warmup.service`、`deepseek-v4-healthcheck.service/.timer`（每 5 分钟检查，重试后才动作，绝不单次超时即重启双机）

## Phase B 完成项

### OpenCode 配置（本机 opencode 1.18.23）
- provider `dgx` → `http://172.19.9.104:8000/v1`
- 模型 `dgx/deepseek-ai/DeepSeek-V4-Flash-0731`（默认）
- context limit 先设 65536（客户端限制），tool_call/reasoning 能力已声明
- variants：`fast` / `think` / `max`（chat_template_kwargs 注入）
- ⚠️ 已知上游 bug：opencode #42876 —— variant 的 body 字段可能在发送前被丢弃（1.18.x）。若 variants 不生效：
  - 默认请求本身即为 fast（thinking off）——日常使用不受影响
  - think/max 可等待上游修复，或经 LiteLLM alias 注入（Phase E）
- Tool calling smoke test PASS：ls → 读文件 → 回答，连续工具调用无 400、无字段丢失 ✅

## ⚠️ 重要发现（需 Phase D 处理）

**>32K prompt 触发 CUDA 崩溃**：
```
CUDA error at sparse_mla_sm120_prefill.cu:67: operation not permitted
Fatal Python error: Aborted → scheduler_0 crashed (exit -6) → 容器自动重启
```
- 复现：40K token prefill 必崩；24K 安全；32K 字符级测试通过但实际 token 数偏小
- 影响：Case D (64K) 当前不可用；context 上限暂被内核 bug 卡住
- 缓解：rank0 有 `--restart unless-stopped` 自动恢复（约 8–10 分钟重新加载）；healthcheck timer 兜底
- 待办（Phase D）：确认 context_length/mem_fraction_static canary 是否可规避；跟踪 flashinfer/sparse_mla 修复

## 内存余量
113 GiB / 121 GiB 使用中 → 余量 **8 GiB**（符合方案"至少 5–8 GB"要求）✅

## 下一步
- Phase C：Open WebUI（控制机 Docker Desktop/WSL2，勿装 DGX）+ RAG
- Phase D：SGLang canary 调参（重点解决 >32K 崩溃后再测 64K/96K/128K）
- Phase E：LiteLLM Gateway + API key + 别名
