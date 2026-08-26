# DeepSeek-V4-Flash-0731 NIM→vLLM 迁移报告

> 版本：v1.0
> 时间：2026-08-26 19:30 +08:00
> 结论：**迁移核心目标全部达成**（40K CUDA crash 解决，64K 稳定，吞吐 2x）

## 1. 迁移目标回顾

原 NIM 1.11.0-variant 存在 P0：>32K prompt 触发 `sparse_mla_sm120_prefill.cu` CUDA crash。
迁移到 pinned vLLM runtime，目标：40K/64K 稳定 + 0 crash。

## 2. 最终配置（pinned）

| 项 | 值 |
|---|---|
| Runtime digest | `ghcr.io/liquidgravityai/2x-dgx-spark-deepseek-v4-flash-0731@sha256:676a1c89...` |
| 本地 tag | `pinned-676a1c89`（arm64，24.8GB） |
| 模型 revision | `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`（与 NIM 缓存 sha256 一致，复用） |
| 配方 commit | `8a161c09f1269ee8ea18ba324b4682d0712f4e4a` |
| vLLM 版本 | 0.26.1rc0 + dspark.r16 + FlashInfer 1ac6942 + CUDA 13.2 |
| 关键参数 | MAX_MODEL_LEN=65536、TP=2、nnodes=2、prefix caching、DSpark(spec=5) |

## 3. 分布式验收

- **world_size=2**：Node0 rank0 (TP rank 0) + Node1 rank1 (TP rank 1) ✅
- **master_addr=192.168.100.10**（CX-7 直连）✅
- NCCL 2.30.4+cuda13.2，PYNCCL all-reduce backend ✅
- 推理时 CX-7 有 RX/TX 流量（数据面走 RoCE）✅
- 模型 155G safetensors 双机加载（InstantTensor，~6.6GB/s）✅

## 4. 核心成果：上下文 P0 解决

### 4.1 上下文矩阵（warmup 1 + 正式 3，全 3/3）
| 级别 | 结果 | E2E |
|---|---|---|
| 8K / 16K / 24K / 32K | 3/3 | 1.6-1.8s |
| **40K** | **3/3** ✅ | 1.5s |
| 48K | 3/3 | 1.9s |
| 64K | 3/3 | 1.7s |

### 4.2 真 64K actual-token（61509 tokens）3/3 通过
- 冷 prefill 16.7s → prefix cache 命中 0.7s

### 4.3 Prefix Cache（Phase 15）
- 64K 共享前缀：TTFT **16.7s → 0.7s（改善 95.8%）**

## 5. 功能验收

| 项 | 结果 |
|---|---|
| API Smoke（/health, /v1/models, chat） | VLLM_DGX_OK ✅ |
| Tool calling（tool_choice=auto） | 正常返回 tool_calls ✅ |
| Reasoning | thinking=false 无推理 / true 有推理 / effort=max ✅ |
| 输出正确性 | JSON 合法、中英文正常、Rust/Python 代码完整 ✅ |
| ⚠️ 注意 | 默认 thinking=true 时小 max_tokens 会耗尽于推理，编程用 Fast（thinking=false） |

## 6. Benchmark vs NIM（decode tok/s）

| 场景 | NIM | vLLM | 提升 |
|---|---|---|---|
| 1K | 19.6 | **47.3** | 2.4x |
| 8K | 21.9 | **43.1** | 2.0x |
| TTFT | 0.26-0.40s | 0.28-0.41s | 持平 |

DSpark speculative decoding 生效，吞吐翻倍。

## 7. 回滚方式（NIM 完整保留）

```bash
# 停 vLLM
cd ~/deepseek-v4-vllm/upstream && docker compose --env-file .env.canary64 down
# 恢复 NIM（容器未删除）
docker start deepseek-v4-rank0   # Node 0，等 API 就绪
docker start deepseek-v4-rank1   # Node 1
curl -f http://172.19.51.123:8000/v1/models
```

## 8. 镜像获取排障经验（重要）

1. **ghcr 下载**：本机代理 7890（FlClash）可用，出口 18.180.248.63
2. **per-connection 限速**：单连接 ~5.7MB/s，8 连接 ~6.7-10.5MB/s；>16 并发触发代理崩溃
3. **curl -C - 续传 bug**：在 ghcr CDN 下卡死（chunk 重试数百次不动）；改为「失败后整个 8MB range 从头重下」后 1.46GB 层 74s 完成
4. **最终方案**：串行整层下载（curl 内建 -C - 续传单文件）最稳定，18MB/s
5. 国内镜像源（daocloud/1ms/xuanyuan）无此镜像
6. docker load 不识别 OCI layout → 需转 docker-archive（gzip 层解压）+ `docker load`；或 `ctr -n moby images import`（但 docker overlay2 存储下不可见）

## 9. 待办

- [ ] Phase 20 Soak：30min（进行中）→ 2h → 8h
- [ ] Phase 21：128K canary（仅当 64K+8h PASS 后）
- [ ] 正式切换：客户端指向 vLLM :18090，或经 LiteLLM :4000
- [ ] NIM 容器/镜像/缓存保留至正式切换后清理

## 10. 服务地址

- vLLM 生产候选：`http://172.19.51.123:18090/v1`（model: `deepseek-v4-flash-0731`）
- NIM 回滚：`http://172.19.51.123:8000/v1`
