# 双 DGX Spark + DeepSeek-V4-Flash-0731
# 个人日常办公与编程后期优化方案（Cursor 可直接执行）

> 版本：v1.0  
> 日期：2026-08-26  
> 适用环境：2 × NVIDIA DGX Spark GB10，DeepSeek-V4-Flash-0731 已通过 NVIDIA NIM 双节点部署  
> 目标：把已经“能跑”的双 DGX 推理集群，优化成 **稳定、低延迟、适合日常办公、论文/文档分析和项目级编程的个人 AI 基础设施**。  
> 执行方式：将本文档交给 Cursor Agent，严格按阶段实施；任何涉及 NIM/SGLang 参数修改必须先做 canary/回滚。

---

## 0. 当前基线与设计结论

### 0.1 已确认的实际部署状态

当前环境已经完成：

| 项目 | 当前状态 |
|---|---|
| Node 0 | `172.19.51.123`，Rank 0 / API |
| Node 1 | `172.19.49.159`，Rank 1 |
| CX-7 直连 | `192.168.100.10` ↔ `192.168.100.11` |
| 链路速率 | 200 Gb/s |
| RDMA | PASS，约 13 GB/s |
| NCCL | PASS，16 GB all_gather busbw ~22.2 GB/s |
| NIM | `deepseek-v4-flash-0731:1.11.0-variant` |
| API | `http://172.19.51.123:8000/v1` |
| 模型缓存 | ~190 GB / node |
| 模型权重 | ~177 GB，48 shards，FP8 |
| 内存占用 | ~112 GB / 121 GB / node |
| 单请求 | 1K：14.4 tps；8K：8.4 tps；32K：6.5 tps |
| 并发 2 | 聚合 ~22.4 tps |
| 并发 4 | 聚合 ~34.0 tps |

### 0.2 当前瓶颈判断

当前不是 GPU 没工作，也不是 RDMA/NCCL 没走高速链路。

从现有结果判断，后续主要瓶颈是：

```text
长 prompt
   ↓
distributed prefill
   ↓
TTFT 增大
   ↓
交互式编程体验下降
```

因此后续优化的优先级应为：

```text
P0  稳定性/可回滚
P1  warmup + TTFT + prefix/radix cache
P2  本地编程客户端
P3  办公/RAG 工作台
P4  上下文与 KV cache 容量
P5  并发和 Agent 工作流
P6  可观测性、自动恢复
P7  多模型/快速补全
```

而不是：

```text
继续折腾 RDMA/NCCL
继续随意提升 context
继续无依据提高 mem_fraction_static
```

---

# 1. 最终目标架构

推荐把系统拆成 **推理平面** 和 **使用平面**。

```text
┌────────────────────────────── Personal PC / Workstation ──────────────────────────────┐
│                                                                                       │
│  OpenCode / Cursor / VS Code          Open WebUI                                      │
│       编程 Agent                    办公 / 文档 / 论文                                  │
│           │                             │                                               │
│           └───────────────┬─────────────┘                                               │
│                           │ OpenAI-compatible API                                       │
└───────────────────────────┼────────────────────────────────────────────────────────────┘
                            │
                     可选 LiteLLM Gateway
                     API Key / alias / queue
                            │
                     ┌──────▼─────────┐
                     │ DGX Spark #0   │
                     │ NIM Rank 0     │
                     │ API :8000      │
                     └──────┬─────────┘
                            │
                     CX-7 / RoCE / NCCL
                         200 Gb/s
                            │
                     ┌──────▼─────────┐
                     │ DGX Spark #1   │
                     │ NIM Rank 1     │
                     └────────────────┘
```

## 1.1 基本原则

### 推理平面

只负责：

- DeepSeek-V4-Flash-0731
- NIM / SGLang
- CX-7 / NCCL
- 模型缓存
- 健康检查
- benchmark

### 使用平面

尽量放在个人电脑/控制机：

- OpenCode
- Open WebUI
- RAG embedding
- 文档库
- 项目记忆
- LiteLLM（可选）
- 日志与 benchmark 报表

### 为什么不把所有工具都塞进 DGX

当前每个节点已经使用约：

```text
112 / 121 GB
≈ 92.6%
```

DGX Spark 使用统一内存。

因此不建议在 DGX 上继续部署：

- 大型 embedding 模型
- Elasticsearch
- 大型 PostgreSQL
- Qdrant 大库
- 多个 Web UI worker
- 第二个大型 LLM

这些都会抢占模型所需 UMA。

---

# 2. Cursor 第一阶段：冻结现有稳定基线

此阶段 **不调任何模型参数**。

目标：

> 先把当前能工作的版本变成可恢复、可比较的“Golden Baseline”。

建立：

```text
/opt/deepseek-v4/ops/
├── current/
├── configs/
├── scripts/
├── benchmark/
├── logs/
└── reports/
```

## 2.1 保存当前容器信息

Node 0：

```bash
sudo mkdir -p /opt/deepseek-v4/ops/{current,configs,scripts,benchmark,logs,reports}

docker inspect deepseek-v4-rank0 \
  > /opt/deepseek-v4/ops/current/rank0-inspect.json

docker top deepseek-v4-rank0 -eo pid,args \
  > /opt/deepseek-v4/ops/current/rank0-process.txt

docker exec deepseek-v4-rank0 env | sort \
  > /opt/deepseek-v4/ops/current/rank0-env.txt
```

Node 1：

```bash
sudo mkdir -p /opt/deepseek-v4/ops/{current,configs,scripts,benchmark,logs,reports}

docker inspect deepseek-v4-rank1 \
  > /opt/deepseek-v4/ops/current/rank1-inspect.json

docker top deepseek-v4-rank1 -eo pid,args \
  > /opt/deepseek-v4/ops/current/rank1-process.txt

docker exec deepseek-v4-rank1 env | sort \
  > /opt/deepseek-v4/ops/current/rank1-env.txt
```

## 2.2 保存系统配置

Node 0 / Node 1：

```bash
cp -a /etc/netplan/40-cx7-deepseek.yaml \
  /opt/deepseek-v4/ops/current/ 2>/dev/null || true

systemctl cat proxy-tunnel2 \
  > /opt/deepseek-v4/ops/current/proxy-tunnel2.service.txt 2>/dev/null || true

docker info \
  > /opt/deepseek-v4/ops/current/docker-info.txt
```

## 2.3 禁止事项

在后续优化完成前，Cursor 不得：

```text
升级 NIM 主版本
删除 1.11.0-variant 镜像
修改 CX-7 网络
修改 NCCL
重装 NVIDIA 驱动
重装 CUDA
删除模型 cache
```

---

# 3. Cursor 第二阶段：建立“暖机后”真实性能基线

当前已有 benchmark 包含首次 autotuning。

这对验证部署是合理的，但不适合作为后续优化比较基线。

必须重新测：

```text
cold
warm
cache-hit
cache-miss
```

四类性能。

---

## 3.1 创建 benchmark 脚本

文件：

```text
/opt/deepseek-v4/ops/benchmark/bench_openai.py
```

要求：

- streaming 请求；
- 测量 TTFT；
- 测量 output tokens/s；
- 测量 end-to-end latency；
- 支持重复 N 次；
- 自动丢弃 warmup；
- 输出 median / p95；
- 输出 JSON；
- 不依赖 NIM 的 per-request metrics。

原因：

DeepSeek-V4-Flash-0731 variant 当前不应依赖 NIM 的 KV-cache/per-request 指标来做调优，客户端自行计时更稳定。

---

## 3.2 基准矩阵

### Case A — 快速办公/代码问答

```text
input: 1K
output: 256
concurrency: 1
```

### Case B — 单文件代码分析

```text
input: 8K
output: 512
concurrency: 1
```

### Case C — 模块级代码分析

```text
input: 32K
output: 1024
concurrency: 1
```

### Case D — 项目上下文

```text
input: 64K
output: 1024
concurrency: 1
```

### Case E — 双 Agent

```text
input: 8K
output: 512
concurrency: 2
```

### Case F — 四 Agent

```text
input: 8K
output: 512
concurrency: 4
```

---

## 3.3 正确测法

每个 case：

```text
warmup × 2
正式 × 5
```

最终使用：

```text
median
p95
```

不要用单次结果。

---

## 3.4 验收指标

至少记录：

```text
TTFT
TPOT
decode tok/s
end-to-end latency
request success rate
Node0 memory
Node1 memory
Node0 GPU util
Node1 GPU util
CX-7 traffic
```

建立：

```text
reports/warm-baseline-YYYYMMDD-HHMMSS.md
```

---

# 4. 第三阶段：先优化 Radix/Prefix Cache，而不是“盲目加 KV”

这是最适合个人编程场景的优化。

## 4.1 原因

编程 Agent 的请求通常是：

```text
相同 system prompt
+
相同项目规则
+
相同仓库背景
+
相同历史上下文
+
新的用户任务
```

因此大量前缀是重复的。

如果前缀 cache 命中：

```text
第一次：
完整 prefill

第二次：
复用相同前缀
       ↓
减少 prefill
       ↓
降低 TTFT
```

---

## 4.2 DeepSeek-V4-Flash-0731 的重要事实

当前 NVIDIA 文档说明：

```text
DSpark speculative decoding
默认已开启
```

因此：

**不要再重复叠加 EAGLE/DSPARK 启动参数。**

同时不要设置：

```text
DISABLE_RADIX_CACHE=1
```

除非专门做 cache-off 对照实验。

---

## 4.3 Cursor 验证当前 Radix Cache

先检查：

```bash
docker exec deepseek-v4-rank0 env | grep -E \
'DISABLE_RADIX_CACHE|KV_CACHE|RADIX' || true
```

检查真实 SGLang 启动参数：

```bash
docker top deepseek-v4-rank0 -eo pid,args
```

确认没有：

```text
--disable-radix-cache
```

---

## 4.4 Cache 命中实验

构造：

```text
40K token 固定项目背景
+
Question A
```

然后立即：

```text
同一个 40K 固定项目背景
+
Question B
```

比较：

```text
TTFT_A
TTFT_B
```

目标：

```text
TTFT_B 显著低于 TTFT_A
```

---

## 4.5 编程 Prompt 必须按“稳定前缀”设计

统一顺序：

```text
1. System instruction
2. Project persistent context
3. Architecture rules
4. Coding style
5. Stable repository context
6. Conversation history
7. Current task
```

不要每一轮随机改变：

```text
system prompt
规则文件顺序
项目说明顺序
```

否则会降低 prefix cache 命中率。

---

# 5. 第四阶段：建立三个推理模式

个人办公与编程不应该每个请求都开最大推理。

DeepSeek-V4-Flash-0731 当前 NIM 支持：

```text
Non-think
Think
Think Max
```

建议映射为：

| Profile | 用途 | 默认 |
|---|---|---|
| Fast | 日常问答、小修改、翻译、摘要 | 是 |
| Think | Debug、代码设计、论文推理 | 按需 |
| Think Max | 架构、大型重构、困难数学问题 | 极少 |

---

## 5.1 Fast

请求：

```json
{
  "chat_template_kwargs": {
    "thinking": false
  }
}
```

适用于：

- 日常办公问答
- 邮件润色
- 文档总结
- 小段代码
- shell 命令
- API 查询
- 简单 bug

---

## 5.2 Think

```json
{
  "chat_template_kwargs": {
    "thinking": true
  }
}
```

适用于：

- Debug
- Rust 生命周期/并发问题
- 系统设计
- 算法分析
- 论文推理
- 复杂代码 review

---

## 5.3 Think Max

```json
{
  "chat_template_kwargs": {
    "thinking": true
  },
  "reasoning_effort": "max"
}
```

只用于：

- 项目级架构修改
- 难以定位的系统错误
- 高难度数学推导
- 大规模重构方案

---

# 6. 第五阶段：OpenCode 作为主编程客户端

建议：

```text
OpenCode = 主力 Agent 编程
Cursor   = 编辑器 + 人工审查 + 辅助
```

原因：

OpenCode 对自定义 OpenAI-compatible endpoint 和 Agent 配置更直接。

---

## 6.1 OpenCode 配置

在项目：

```text
opencode.jsonc
```

加入：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",

  "model": "local-dgx/deepseek-v4",

  "providers": {
    "local-dgx": {
      "name": "Dual DGX DeepSeek V4",
      "package": "@opencode-ai/ai/providers/openai-compatible",

      "settings": {
        "baseURL": "http://172.19.51.123:8000/v1"
      },

      "models": {
        "deepseek-v4": {
          "modelID": "deepseek-ai/DeepSeek-V4-Flash-0731",
          "name": "DeepSeek V4 Flash Local",

          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          },

          "limit": {
            "context": 65536,
            "output": 16384
          },

          "variants": [
            {
              "id": "fast",
              "body": {
                "chat_template_kwargs": {
                  "thinking": false
                }
              }
            },

            {
              "id": "think",
              "body": {
                "chat_template_kwargs": {
                  "thinking": true
                }
              }
            },

            {
              "id": "max",
              "body": {
                "chat_template_kwargs": {
                  "thinking": true
                },
                "reasoning_effort": "max"
              }
            }
          ]
        }
      }
    }
  }
}
```

### 第一阶段 context 暂时填写

```text
65536
```

这是 **OpenCode 客户端限制**，并不等于模型本身最大能力。

后面验证 128K 稳定后再改：

```text
131072
```

---

# 7. Tool Calling 兼容性必须单独验收

DeepSeek-V4-Flash-0731 的 DSpark speculative decoding 与 constrained decoding 存在限制。

当前应遵守：

```text
tool_choice = auto
```

不要强制：

```text
tool_choice = required
```

也不要强制指定某一个函数。

---

## 7.1 Cursor/OpenCode 测试

让 Agent：

```text
读取当前目录
```

然后：

```text
读取 Cargo.toml
```

然后：

```text
修改一个临时文件
```

最后：

```text
执行 git diff
```

必须验证：

- tools 能正常返回；
- 不出现 HTTP 400；
- tool call parser 没有丢字段；
- stream 正常；
- Agent 可以连续多轮调用工具。

如果出现：

```text
structured output
guided decoding
tool_choice required
```

相关错误，

优先调整客户端请求模式，而不是修改 DeepSeek/NIM。

---

# 8. 第六阶段：建立项目级持久上下文

不要依赖 128K/256K context 来“记住整个项目”。

每个项目建立：

```text
.ai/
├── README.md
├── architecture.md
├── constraints.md
├── coding-rules.md
├── decisions.md
├── current-status.md
├── known-issues.md
└── glossary.md
```

---

## 8.1 README.md

内容：

```text
项目是什么
主要模块
目录结构
入口
如何运行
如何测试
```

---

## 8.2 architecture.md

维护：

```text
架构
依赖关系
数据流
关键接口
```

---

## 8.3 constraints.md

例如：

```text
必须 AST-driven
禁止 hard-code
保持 backward compatibility
不新增中间件
```

---

## 8.4 decisions.md

记录：

```text
ADR-001
ADR-002
...
```

防止 Agent 下一次推翻之前正确决策。

---

## 8.5 current-status.md

每次较大的 Cursor/OpenCode 工作结束后更新：

```text
已经完成什么
当前在哪
下一步是什么
```

---

## 8.6 作用

这比单纯：

```text
64K → 128K → 256K
```

更重要。

因为：

```text
长期记忆 ≠ 超长 context
```

---

# 9. 第七阶段：代码工作流标准化

推荐：

```text
Plan
 ↓
Inspect
 ↓
Implement
 ↓
Test
 ↓
Review
 ↓
Commit
```

不要：

```text
需求
 ↓
直接修改几十个文件
```

---

## 9.1 Agent 分工

### Planner

只做：

```text
读取
分析
方案
任务拆解
```

不写代码。

### Builder

负责：

```text
实现
测试
修复
```

### Reviewer

负责：

```text
git diff
架构约束
潜在回归
测试覆盖
```

---

## 9.2 为什么适合当前双 DGX

你当前：

```text
concurrency 2 ≈ 22.4 tps aggregate
concurrency 4 ≈ 34.0 tps aggregate
```

说明双机有一定 batch/concurrency 收益。

但个人使用重点是低延迟。

所以推荐日常：

```text
并发 1~2
```

不要默认：

```text
4 个 Agent 同时疯狂执行
```

需要大型 review 时才允许 3~4。

---

# 10. 第八阶段：个人办公入口使用 Open WebUI

编程之外，建议部署：

```text
Open WebUI
```

用途：

- 文件问答
- PDF/论文分析
- Word/Excel 内容分析
- 日常写作
- 邮件草稿
- 会议总结
- 知识库
- 常用 Prompt

---

## 10.1 Open WebUI 不建议部署在 DGX

推荐放在：

```text
个人 Windows + Docker Desktop
或
WSL2
或
一台普通 Linux 控制机
```

原因：

DGX 已经只有约 9 GB 级内存余量。

---

## 10.2 Docker 示例

在控制机：

```bash
docker volume create open-webui
```

启动：

```bash
docker run -d \
  --name open-webui \
  --restart unless-stopped \
  -p 127.0.0.1:3000:8080 \
  -v open-webui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

打开：

```text
http://127.0.0.1:3000
```

配置：

```text
Admin Settings
→ Connections
→ OpenAI Compatible
```

URL：

```text
http://172.19.51.123:8000/v1
```

Model：

```text
deepseek-ai/DeepSeek-V4-Flash-0731
```

---

# 11. 办公知识库/RAG

不要把所有办公文档全部塞进 prompt。

使用 RAG。

推荐知识库划分：

```text
Knowledge/
├── Research-Papers/
├── Tex2Doc/
├── Scheduling-System/
├── Experiment-Console/
├── Patents/
├── Personal-Templates/
└── Technical-Docs/
```

---

## 11.1 Embedding 不占用 DGX

建议 embedding 放：

```text
个人电脑 CPU
```

初期可直接使用 Open WebUI 默认本地 embedding。

如果中英文材料很多，后续可以独立部署：

```text
BAAI/bge-m3
```

作为 multilingual embedding。

---

## 11.2 不建议一开始引入复杂向量数据库

个人用户：

```text
Open WebUI 默认存储
```

先够用。

暂时不新增：

```text
Milvus
Elasticsearch
Weaviate
大型 Qdrant cluster
```

需要几十万/百万文档后再升级。

---

# 12. 第九阶段：上下文长度调优

这是后续真正需要谨慎做的地方。

## 12.1 不要直接设置 NIM_MAX_MODEL_LEN

针对 DeepSeek-V4-Flash-0731 variant，当前 NVIDIA 文档明确标记：

```text
NIM_MAX_MODEL_LEN
Not Supported
```

因此不要通过：

```bash
-e NIM_MAX_MODEL_LEN=131072
```

来实现。

---

## 12.2 优先采用 NIM_CONFIG_FILE / SGLang 参数

在当前镜像确认支持后，建立：

```text
/opt/deepseek-v4/ops/configs/sglang-64k.yaml
```

例如：

```yaml
context_length: 65536
```

高级调参时可以加入：

```yaml
mem_fraction_static: <经过基线确认的值>
```

然后挂载：

```text
NIM_CONFIG_FILE
```

但必须注意：

**当前生产镜像是 1.11.0-variant，而后续 NVIDIA 文档可能针对更新版 NIM。**

Cursor 必须先在当前容器内检查：

```bash
docker exec deepseek-v4-rank0 \
  python -m sglang.launch_server --help 2>/dev/null | \
  grep -E 'context-length|mem-fraction-static|max-running-requests'
```

以及查看实际 process args。

确认参数存在后才能使用。

---

# 13. mem_fraction_static 的正确调法

不要直接从：

```text
0.7 → 0.85
```

因为当前实际 UMA 已经：

```text
112 / 121 GB
```

必须先查当前 effective value。

---

## 13.1 Cursor 先读取

```bash
docker top deepseek-v4-rank0 -eo pid,args
```

搜索：

```text
mem-fraction-static
```

如果没有显示，则检查 NIM 配置和 server info。

---

## 13.2 调优方式

以当前值：

```text
M
```

为基准。

只测试：

```text
M - 0.02
M
M + 0.02
```

例如如果当前：

```text
0.80
```

才测试：

```text
0.78
0.80
0.82
```

不能直接跳：

```text
0.9
```

---

## 13.3 每次验收

必须：

```text
启动成功
32K prompt PASS
64K prompt PASS
concurrency 2 PASS
30 min soak PASS
无 OOM
无 swap thrash
```

---

# 14. 推荐 context 路线

不要直接追 1M。

个人办公/代码建议：

### Profile A

```text
64K
```

作为默认。

### Profile B

```text
96K
```

如果大型仓库需要。

### Profile C

```text
128K
```

项目级分析。

### 256K+

只作为专门任务 profile。

---

## 14.1 最终默认建议

```text
日常默认 = 64K
大型分析 = 128K
```

原因：

长 context 的代价不是只有内存：

```text
context ↑
  ↓
prefill ↑
  ↓
TTFT ↑
```

当前 baseline 已经说明 32K 时单请求速度明显下降。

---

# 15. 不要配置 KV Host Offload

DeepSeek-V4-Flash-0731 当前 NIM variant 不支持：

```text
NIM_ENABLE_KV_CACHE_HOST_OFFLOAD
NIM_KV_CACHE_HOST_MEM_FRACTION
```

因此 Cursor 不应尝试：

```bash
-e NIM_ENABLE_KV_CACHE_HOST_OFFLOAD=1
```

这不会成为当前环境的正确优化路径。

优先优化：

```text
Radix cache
stable prefix
context length
mem_fraction_static
并发
```

---

# 16. DSpark 不需要再开启

当前 NIM 对 DeepSeek-V4-Flash-0731：

```text
DSpark speculative decoding
默认开启
```

因此不要重新添加：

```text
EAGLE
MTP draft
speculative algorithm override
```

除非 NVIDIA 后续文档明确支持该组合并且做 A/B test。

---

# 17. 第十阶段：自动 Warmup

当前首次请求存在 autotuning。

因此每次：

```text
docker restart
系统重启
模型重启
```

之后自动做 warmup。

---

## 17.1 warmup.sh

创建：

```text
/opt/deepseek-v4/ops/scripts/warmup.sh
```

逻辑：

```text
等待 /v1/models
 ↓
1K prompt × 2
 ↓
8K prompt × 1
 ↓
输出完成时间
```

不需要 32K warmup。

---

## 17.2 systemd

建立：

```text
deepseek-v4-warmup.service
```

依赖：

```text
deepseek-v4 cluster ready
```

执行：

```text
warmup.sh
```

---

# 18. 第十一阶段：轻量 API Gateway

个人环境不是必须上复杂网关。

第一阶段可以直接：

```text
OpenCode/OpenWebUI → NIM:8000
```

稳定后再加入：

```text
LiteLLM
```

主要为了：

- API key；
- model alias；
- 统一 endpoint；
- 后续增加其他模型；
- request logging 可控；
- 限流/队列。

---

## 18.1 推荐最终接口

应用只使用：

```text
http://172.19.51.123:4000/v1
```

而：

```text
:8000
```

变成内部调试接口。

---

## 18.2 模型 alias

例如：

```text
deepseek-local
deepseek-coding
deepseek-office
```

均可指向：

```text
deepseek-ai/DeepSeek-V4-Flash-0731
```

不同客户端用不同 request profile。

---

## 18.3 日志原则

个人办公涉及：

```text
论文
代码
文档
个人内容
```

因此 Gateway 默认：

```text
不记录完整 prompt
不记录 completion
```

只保存：

```text
timestamp
latency
status
token count
model
```

---

# 19. 第十二阶段：API 安全

目前 API：

```text
172.19.51.123:8000
```

如果管理网中其他机器可访问，则存在未经授权调用风险。

最终建议：

```text
NIM 8000
仅内部/受信访问

LiteLLM 4000
使用 API Key
```

如果暂时不用 LiteLLM，则至少通过：

```text
nftables/ufw
```

限制仅允许个人电脑 IP。

Cursor 修改 firewall 前必须：

```text
确认 SSH 管理口不会被锁死
创建 rollback
保持现有 SSH session
```

---

# 20. 第十三阶段：监控但保持轻量

当前不需要搭完整企业 Prometheus 集群。

先建立：

```text
ops/status.sh
```

输出：

```text
Node0 container
Node1 container
/v1/models
memory
nvidia-smi
CX-7
NCCL errors
API response
```

---

## 20.1 监控指标

个人使用最重要：

```text
API availability
TTFT
decode tok/s
memory
GPU util
CX-7 RX/TX
container restart count
```

---

## 20.2 /v1/metrics

可以采集 NIM metrics。

但注意：

当前 DeepSeek V4 variant 的 metrics 并不提供完整 KV-cache 指标。

因此 KV/cache 效果要通过：

```text
重复 prefix benchmark
```

间接判断。

---

# 21. 第十四阶段：自动恢复

实现：

```text
deepseek-v4-healthcheck.timer
```

每 5 分钟：

```text
GET /v1/models
```

如果失败：

```text
1. 再试 2 次
2. 检查 rank1
3. 保存日志
4. 只在确认 container dead 时 restart
```

不要：

```text
一次 HTTP timeout
↓
立即重启两台
```

---

# 22. 第十五阶段：办公与编程模式分离

建议形成两个入口。

## 办公

```text
Open WebUI
DeepSeek Fast
64K
RAG
```

用于：

- PDF
- Word
- 邮件
- 方案
- 摘要
- 论文阅读

## 编程

```text
OpenCode
DeepSeek Fast/Think
64K / 128K
tool calling
```

用于：

- code inspect
- refactor
- test
- shell
- git
- architecture

---

# 23. 一个非常重要的长期优化：增加“小模型快车道”

DeepSeek-V4-Flash 双 Spark 的优势：

```text
质量高
复杂任务强
长上下文强
Agent 强
```

但它不是最适合：

```text
逐字代码补全
100~300 ms 级 autocomplete
```

因此未来推荐：

```text
小模型
  ↓
autocomplete / quick edit

DeepSeek V4
  ↓
Agent / reasoning / repo task
```

---

## 23.1 不要现在强行同驻 DGX

当前两台每台只有约：

```text
9 GB 级可用余量
```

不适合再常驻一个 27B。

---

## 23.2 后续可以考虑

在个人电脑 GPU 或另外一台机器：

```text
7B~14B coder
```

负责：

```text
FIM
autocomplete
小 patch
```

双 DGX 继续负责：

```text
复杂 coding agent
```

---

# 24. 推荐的日常策略

## 轻任务

```text
Fast
context ≤ 32K
concurrency 1
```

## 普通编程

```text
Fast / Think
context ≤ 64K
concurrency 1
```

## 大模块 Debug

```text
Think
64K~128K
```

## 架构/重大重构

```text
Think Max
128K
```

## 多 Agent

```text
concurrency 2
```

默认不超过：

```text
2
```

---

# 25. Cursor 应实现的目录

建议在部署项目中创建：

```text
deepseek-local-ai/
├── README.md
├── configs/
│   ├── opencode.jsonc
│   ├── openwebui/
│   ├── litellm/
│   └── nim/
├── scripts/
│   ├── snapshot-current.sh
│   ├── status.sh
│   ├── warmup.sh
│   ├── benchmark.py
│   ├── benchmark-suite.sh
│   ├── cache-benchmark.py
│   ├── start.sh
│   ├── stop.sh
│   ├── restart-safe.sh
│   └── rollback.sh
├── systemd/
│   ├── deepseek-v4-warmup.service
│   ├── deepseek-v4-healthcheck.service
│   └── deepseek-v4-healthcheck.timer
├── benchmark/
│   └── cases/
└── reports/
```

---

# 26. 分阶段实施顺序

## Phase A — 零风险

Cursor 完成：

- [ ] snapshot 当前配置
- [ ] warm benchmark
- [ ] status.sh
- [ ] warmup.sh
- [ ] health check
- [ ] cache hit benchmark

**不改 NIM 参数。**

---

## Phase B — 编程接入

- [ ] OpenCode local provider
- [ ] fast / think / max variants
- [ ] tool calling smoke test
- [ ] `.ai/` persistent context
- [ ] Planner / Builder / Reviewer

---

## Phase C — 办公接入

- [ ] Open WebUI
- [ ] OpenAI-compatible connection
- [ ] Knowledge/RAG
- [ ] embedding 放控制机
- [ ] 办公知识库分类

---

## Phase D — NIM/SGLang 性能调参

仅在 A/B/C 全部稳定后：

- [ ] 读取真实 SGLang effective args
- [ ] 验证 NIM_CONFIG_FILE
- [ ] 64K
- [ ] 96K
- [ ] 128K
- [ ] mem_fraction ±0.02
- [ ] concurrency 1/2/4
- [ ] 30 min soak

---

## Phase E — Gateway/Security

- [ ] LiteLLM
- [ ] API key
- [ ] model aliases
- [ ] 8000 内部化
- [ ] 4000 对客户端
- [ ] 不记录 prompt/completion

---

## Phase F — 长期优化

- [ ] 小 coder 模型快车道
- [ ] autocomplete
- [ ] daily benchmark trend
- [ ] NIM 新版本 canary
- [ ] 8h soak
- [ ] 自动恢复

---

# 27. 不建议做的事项

当前阶段明确不建议：

```text
1. 立刻从 NIM 1.11 升到最新版
2. 立刻改成裸 SGLang
3. 直接把 context 拉到 1M
4. 开 KV host offload
5. 再手工启用 DSpark/EAGLE
6. 盲目把 mem_fraction_static 拉到 0.9
7. 在 DGX 上部署大型向量数据库
8. 同时常驻第二个 27B/70B 模型
9. 默认 concurrency=4+
10. 为了办公 RAG 把全部文档塞进 prompt
```

---

# 28. 推荐最终个人使用体验

完成后：

## 打开 OpenCode

```text
opencode
```

选择：

```text
Dual DGX DeepSeek V4
```

日常：

```text
#fast
```

复杂任务：

```text
#think
```

极难：

```text
#max
```

---

## 打开浏览器

```text
http://127.0.0.1:3000
```

使用 Open WebUI：

```text
聊天
论文
文档
PDF
知识库
写作
```

---

# 29. 最终性能目标

不追求“跑分漂亮”，而是追求日常体验。

推荐 DoD：

### Fast

```text
1K prompt TTFT：尽量 < 2~3 s
8K prompt TTFT：明显优于当前 cold baseline
```

### Cache hit

相同 32K~64K prefix 第二次请求：

```text
TTFT 至少下降 30%
```

理想：

```text
50%+
```

### Coding

```text
工具调用成功率 ≥ 99%
无频繁 HTTP 400
git/test/shell 可连续完成
```

### Stability

```text
8h 连续使用
0 OOM
0 rank disconnect
0 NCCL error
0 人工重启
```

### Memory

保持：

```text
至少 5~8 GB 安全余量/node
```

不要把 UMA 吃满。

---

# 30. Cursor 最终总任务 Prompt

将下面内容直接交给 Cursor Agent：

```text
目标：
把已经成功运行的 2×DGX Spark + DeepSeek-V4-Flash-0731
从“部署成功”优化为适合个人日常办公、论文/文档分析和项目级编程的
稳定本地 AI 基础设施。

现状：
Node0 = 172.19.51.123
Node1 = 172.19.49.159
Direct Link = 192.168.100.10 <-> 192.168.100.11
NIM API = http://172.19.51.123:8000/v1
Model = deepseek-ai/DeepSeek-V4-Flash-0731
NIM image = nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant

现有部署已经 PASS：
- CX-7 200Gbps
- RDMA
- NCCL
- Rank0/Rank1
- API
- 双节点推理

严禁：
- 不重装驱动/CUDA
- 不修改 CX-7 网络
- 不删除现有 NIM image/cache
- 不直接升级 NIM
- 不直接把 context 改到 1M
- 不设置 NIM_MAX_MODEL_LEN
- 不启用 KV host offload
- 不重复开启 DSpark/EAGLE
- 不盲目提高 mem_fraction_static

执行：

PHASE 1
1. snapshot 当前 rank0/rank1 docker inspect、env、process args。
2. 保存 netplan、systemd、Docker 配置。
3. 创建 /opt/deepseek-v4/ops。
4. 当前部署作为 Golden Baseline。

PHASE 2
5. 编写 streaming benchmark 脚本。
6. 测 TTFT、TPOT、decode tok/s、E2E latency。
7. 每个 case warmup 2 次，正式 5 次，输出 median/p95。
8. 测 1K/8K/32K/64K，concurrency 1/2/4。
9. 保存 warm-baseline report。

PHASE 3
10. 检查 Radix cache 未被关闭。
11. 构造重复 32K/64K shared-prefix benchmark。
12. 记录 cache-hit TTFT 改善。
13. 不设置 DISABLE_RADIX_CACHE=1。

PHASE 4
14. 创建 OpenCode custom OpenAI-compatible provider。
15. baseURL 指向 http://172.19.51.123:8000/v1。
16. Model ID = deepseek-ai/DeepSeek-V4-Flash-0731。
17. 创建 fast / think / max 三个 variants。
18. 初始 context client limit = 65536。
19. tools capability = true。
20. 做 ls/read/edit/git diff/shell 连续 tool calling smoke test。
21. 如 tool calling 失败，首先检查 tool_choice。
    DeepSeek V4 0731 DSpark 环境不得使用 constrained tool_choice=required。
    保持 tool_choice auto。

PHASE 5
22. 为项目建立 .ai/
    README.md
    architecture.md
    constraints.md
    coding-rules.md
    decisions.md
    current-status.md
    known-issues.md
    glossary.md。
23. 配置 Planner / Builder / Reviewer 工作流。

PHASE 6
24. 在个人控制机部署 Open WebUI，不部署到 DGX。
25. 连接 NIM OpenAI-compatible API。
26. 建立 Knowledge/RAG。
27. embedding 在控制机运行。
28. 不新增大型中间件/向量集群。

PHASE 7
29. 创建 status.sh。
30. 创建 warmup.sh。
31. 创建 systemd healthcheck。
32. 重启后自动 warmup。
33. healthcheck 不允许单次 timeout 就重启集群。

PHASE 8
34. 只在前面全部 PASS 后开始性能调参。
35. 先读取当前 SGLang effective args。
36. 在当前 1.11.0 container 内确认 NIM_CONFIG_FILE 和
    context_length/mem_fraction_static 支持情况。
37. 采用 canary 配置，不覆盖 Golden Baseline。
38. 测试 64K → 96K → 128K。
39. mem_fraction_static 只能以当前 effective value 为中心 ±0.02。
40. 每个配置必须通过：
    startup
    32K
    64K
    concurrency 2
    30 min soak
    no OOM。

PHASE 9
41. 如需要统一 endpoint，再部署轻量 LiteLLM Gateway。
42. API 对客户端使用 key。
43. 默认不记录完整 prompt/completion。
44. 8000 后续仅作为内部/debug endpoint。

PHASE 10
45. 做 8 小时稳定性测试。
46. 输出最终 optimization-report.md。
47. 对比 Golden Baseline：
    TTFT
    decode tps
    cache hit
    64K latency
    c2 throughput
    memory
    stability。

原则：
任何优化只有在可量化优于 Golden Baseline 且没有降低稳定性时才保留。
任何阶段失败立即回滚该阶段，不破坏现有双机生产服务。
```

---

# 31. 最终推荐

对于当前这两台 DGX，最优路线不是继续追求“最大模型参数”或“最大 context”。

应该把它建设成：

```text
              Personal Local AI
                      │
        ┌─────────────┴────────────┐
        │                          │
     OpenCode                  Open WebUI
     Coding                    Office/RAG
        │                          │
        └─────────────┬────────────┘
                      │
             DeepSeek V4 Flash
              Fast / Think
                      │
              2 × DGX Spark
                  CX-7
```

核心目标：

```text
高质量
+
稳定
+
可复用上下文
+
低 TTFT
+
强工具调用
+
私有办公知识库
```

而不是单纯追求最高 tokens/s。

---

# 32. 官方参考资料

1. NVIDIA NIM — DeepSeek-V4-Flash-0731 / container variant  
   https://docs.nvidia.com/nim/large-language-models/1.15.0/nim-container-variants.html

2. NVIDIA NIM — KV Cache Reuse  
   https://docs.nvidia.com/nim/large-language-models/1.15.0/kv-cache-reuse.html

3. NVIDIA NIM — Configuration  
   https://docs.nvidia.com/nim/large-language-models/1.15.0/configuration.html

4. SGLang — Bench Serving  
   https://docs.sglang.ai/developer_guide/bench_serving

5. OpenCode — Local/OpenAI-compatible Models  
   https://opencode.ai/v2/docs/models

6. OpenCode — Providers  
   https://opencode.ai/v2/docs/providers

7. Open WebUI — OpenAI-Compatible Provider  
   https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/

8. Open WebUI — Knowledge / RAG  
   https://docs.openwebui.com/features/workspace/knowledge/

9. LiteLLM — Gateway  
   https://docs.litellm.ai/
