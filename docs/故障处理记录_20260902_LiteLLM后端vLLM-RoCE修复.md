# LiteLLM 后端故障处理记录 — vLLM 双机不可用(RoCE GID 漂移)

> 记录日期:2026-09-02
> 关联集群:2× DGX Spark GB10(deepseek-v4-flash-dual-dgx)
> 技术根因详见 `deepseek-v4-flash-dual-dgx/reports/incident-20260902-vllm-roce-gid.md`
> 全部凭证/地址:见仓库根目录 `.env`(已 gitignore,禁止提交)

---

## 1. 背景与现象

用户反馈「LiteLLM 模型服务不正常」。现象归纳:

- LiteLLM 网关(`:4000` / `:4443`)进程与 systemd 均正常,`/v1/models` 可返回 200;
- 但 `chat/completions` 全部报错:LiteLLM 日志显示
  `Connection error ... Model Group=deepseek-coding ... Retried: 2 times` → 500;
- 直接探活发现 vLLM 后端 `:18090` 无监听 —— **问题不在网关,在后端**。

## 2. 诊断过程

| 步骤 | 命令/动作 | 结论 |
|---|---|---|
| 1. 服务状态 | `systemctl is-active litellm litellm-https`;`ss -tlnp` | 网关两端口正常监听;18090 无监听 |
| 2. 容器状态(Node0) | `docker ps -a` | `deepseek-v4-flash-0731-0` **Exited(0) 5h 前**;Node1 仍 Up 但为悬挂 worker |
| 3. 崩溃日志 | `docker logs deepseek-v4-flash-0731-0 --tail` | 当日 01:57 `TimeoutError: RPC call to sample_tokens timed out` → EngineDeadError → 干净退出(容器 restart=no,不会自愈) |
| 4. 尝试拉起 | compose `up -d`(Node1 先、Node0 后) | 双机均失败:`ibv_modify_qp failed ... local GID :: remote GID ::` + `NCCL error: unhandled system error` |
| 5. 物理链路 | `ethtool / rdma link / ping 192.168.100.x` | 网卡 200Gb UP、RDMA ACTIVE、ping 通 → **非物理故障** |
| 6. GID 表 | `cat /sys/class/infiniband/rocep1s0f0/ports/1/gids/*` | **发现根因**:RoCEv2 IPv4 GID 现位于 index 4,index 3 已空;而配置固定 `NCCL_IB_GID_INDEX=3` → 读到空 GID `::` |
| 7. 验证候选修复 | `ib_write_bw -d rocep1s0f0 -x 4`(双机) | **13 GB/s 通过**,GID 192.168.100.10 ↔ .11 正确 |

> 说明:index 3 曾在旧布局下是 RoCEv2 IPv4 GID(此前稳定运行 6 天),本次 GID 表布局漂移后 index 3 变空、index 4 才是 RoCEv2 IPv4。链路本身一直健康。

## 3. 修复操作

1. **双机修改 env**(改前自动备份):
   ```bash
   cd ~/deepseek-v4-vllm/upstream
   # 备份:.env.canary64.bak-20260902-144059 / .env.canary128.bak-20260902-144059
   # 内容:IB_GID_INDEX=3 → IB_GID_INDEX=4(作用于 .env.canary64 与 .env.canary128)
   ```
2. **按文档顺序重启双机**(先 Node1 rank1,后 Node0 rank0):
   ```bash
   # Node 1 / Node 0 各自:
   cd ~/deepseek-v4-vllm/upstream
   docker compose --env-file .env.canary128 down
   docker compose --env-file .env.canary128 up -d
   ```
3. **验证通过**(关键证据):
   - Node0 `:18090/health` → 200,约 2 分钟就绪;日志确认 `world_size=2` / NCCL / DSpark 正常;
   - vLLM 直连 chat → `VLLM_DGX_OK`;
   - LiteLLM HTTP `:4000` chat → `GATEWAY_OK`,HTTPS `:4443` `/v1/models` → 200;
   - 从本机客户端(Windows)经 `http://172.19.51.123:4000/v1` 实测 → `FROM_CLIENT_OK`。

## 4. 附带清理(NIM 时代遗留)

经用户确认「NIM 时代遗留可直接清除」,Node0 上执行:

| 单元/目录 | 处置 |
|---|---|
| `deepseek-v4-healthcheck.timer` | `disable` + 删除 |
| `deepseek-v4-healthcheck.service` | 删除(引用的 `/opt/deepseek-v4/ops/scripts/healthcheck.sh` 早已不存在,每 5 分钟 203/EXEC 空转) |
| `deepseek-v4-warmup.service` | 删除(同样指向不存在的脚本) |
| `/opt/deepseek-v4`(空目录) | `rmdir` |

随后 `systemctl daemon-reload` + `reset-failed`。现仅剩 `litellm.service` / `litellm-https.service` 两个本项目服务。

## 5. 当前状态与速查

- 全栈健康:`:18090/health=200`、`:4000` 与 `:4443` `/v1/models=200`,网关推理返回正常;
- 模型别名:`deepseek-local` / `deepseek-coding` / `deepseek-office` → `deepseek-v4-flash-0731`(128K);
- 运维地址与 SSH/API 凭证:一律查根目录 **`.env`**(本文件不再重复明文)。

## 6. 经验与后续建议

1. **启动 vLLM 前核对 GID index**(RoCE GID 布局可能再次漂移):
   ```bash
   for i in 3 4; do echo "$i => $(cat /sys/class/infiniband/rocep1s0f0/ports/1/gids/$i)"; done
   # 以「type=RoCE v2 且值=::ffff:c0a8:640a(Node0)/640b(Node1)」的实际 index 为准,
   # 并同步双机 .env 的 IB_GID_INDEX
   ```
2. `restart=no` 是刻意配置(防 NIM/vLLM 意外争抢内存),意味着故障后**不会自愈**,建议后续加一个仅做「检查 `:18090/health` + 告警」的守护(不要自动重启双机,单次超时不应触发重启)。
3. 旧 `healthcheck/warmup` 已清除,勿再引用 `/opt/deepseek-v4`。

---
*本文档为处理过程说明;更完整的排障细节见 `reports/incident-20260902-vllm-roce-gid.md`。*

---

## 7. 上下文上限强制管理(2026-09-02 追加,运维策略)

### 需求
LiteLLM 层对上下文做硬性管理:**输入超过 125K(125000 tokens)的请求一律 400 拒绝,不允许提交到 vLLM 后端**。

### 为何此前没有管控住(排查结论)
1. `litellm_hooks.py` 的自定义 hook 存在,但硬顶是 `MODEL_MAX_TOKENS−SAFETY_MARGIN = 131072−2048 = 129024`,
   **不是 125K**,约 126K 的请求可被放行;
2. `litellm_config.yaml` 里 `deepseek-local/coding/office` **未声明任何 token 上限**(只有 `claude-sonnet-4-5`
   声明 129024),LiteLLM `/v1/models` 也不上报上限;
3. 崩溃当时(15:57 HKT)该 `deepseek-coding` 请求经网关放行(hook 估算 input=78548),prefill 中途触发
   cuBLAS 崩溃;因容器 `restart=no` 不自愈 → 后端持续不可用 → 网关 500。

### 已实施(双保险,均已实测)
- `litellm_hooks.py`:拒绝线改为 `HARD_INPUT_LIMIT = 125000`,`input > 125000` → HTTP 400「输入过长…已拒绝提交模型」;
  仍保留 `max_tokens` 动态收敛(`输入+输出 ≤ 131072−2048`)。备份:`/home/dgxdeploy/litellm_hooks.py.bak-20260902-170145`。
- `litellm_config.yaml`:4 个别名统一 `model_info.max_input_tokens: 125000`。备份:`...litellm_config.yaml.bak-20260902-170145`。
- 两个网关(litellm :4000 / litellm-https :4443)已重启并验证:
  - 构造估算 128,948 tokens 的 payload → HTTP/HTTPS 均 **400**,vLLM 无任何请求落地(health 恒 200);
  - 正常小请求 → 200 正常推理。

### 关键发现 / 后续建议
- **token 口径**:实测对中文+代码内容,`litellm.token_counter` 估算比 vLLM 真实计数**高约 1.7x**
  (估算 20,712 vs 真实 12,129)。因此 125K 阈值偏保守(不会漏放),但同时也意味着:
  崩溃请求估算仅 78,548,其真实 token 很可能远低于 125K → **本次崩溃本质是该 pinned build 大规模 prefill 的
  稳定性缺陷,并非单纯"顶到 128K"**,125K 上限能挡边界请求,但无法根除该 bug 在中低规模重现的可能。
- 建议后续:
  1. 在 vLLM 侧开启请求级 access log / 指标,记录每次请求的真实 prompt_tokens,便于复现定位;
  2. 若 64K 可接受,可临时切 `canary64`(MAX_MODEL_LEN=65536)规避大 prefill;否则对该崩溃样本提工单给镜像方;
  3. 考虑把 `:18090` 收敛为仅本机/网关可达(防绕过 LiteLLM 直连触发同类问题);
  4. 对 `restart=no` 增加「异常退出才自动拉起 + 冷却」的守护,避免一次坏请求导致长时间停服。

---

## 8. 加固落地(2026-09-02 追加,已实施并验证)

用户选定三项加固,均已在 Node0/双机完成:

### 8.1 收敛 :18090 仅本机可达(防绕过网关直连)
- 文件:
  - `/usr/local/sbin/vllm-port-lockdown.sh`(幂等,iptables)
  - `/etc/systemd/system/vllm-port-lockdown.service`(`enable --now`,开机自应用)
- 规则(INPUT 链,位于 tailscale `ts-input` 之前):
  ```
  1 ACCEPT  tcp -- 127.0.0.1             tcp dpt:18090
  2 REJECT  tcp -- 0.0.0.0/0             tcp dpt:18090 reject-with tcp-reset
  ```
- 验证:主机 `127.0.0.1:18090/health=200`、网关正常;**外部**访问 `172.19.51.123:18090` 已被拒绝(err=7);
  一切外部流量必须走 LiteLLM(125K 管控在网关生效)。

### 8.2 开启 vLLM 请求级日志
- 双机 `~/deepseek-v4-vllm/upstream/compose.yaml` 增加(备份 `compose.yaml.bak-20260902-1709xx`):
  ```yaml
  command: ["--enable-log-requests"]
  ```
  该镜像入口(`deepseek-v4-flash-2x-spark-entrypoint` 以 `"$@"` 收尾)会把它透传给 `vllm serve`。
- 重启后日志可见:
  `INFO [request_logger.py:63] Received request chatcmpl-xxx: params: SamplingParams(...max_tokens=...)`。
- 说明:INFO 级只含 request id + sampling 参数;如需逐 token 明细需 `VLLM_LOGGING_LEVEL=DEBUG`(默认不开,过噪)。
  崩溃定位时配合 LiteLLM hook 的 `input=N` 估算即可还原触发请求。

### 8.3 异常退出自动拉起 watchdog(Node0 编排,双机)
- 文件:
  - `/home/dgxdeploy/ops/watchdog-vllm.sh`(60s 由 timer 触发)
  - `/etc/systemd/system/vllm-watchdog.{service,timer}`(`enable --now`)
- 逻辑:仅当本地 rank0 容器非 running(Exited/Dead/absent)且无维护标记且距上次恢复 >600s 时,
  **先重启 Node1(rank1)再重启 Node0(rank0)**,日志:`~/logs/vllm-watchdog.log`。
- **维护开关**:需要人工停 vLLM(如升级/排查)时先
  `touch /home/dgxdeploy/.vllm_maintenance`,完成后 `rm` 之,否则 watchdog 会抢着重启。
- 演练结果:kill rank0(Exited 137)后 ~60s 内自动重启 node1→node0,195s 恢复 `health=200`。

### 8.4 运维速查(涉及本次新增)
| 动作 | 命令 |
|---|---|
| 查看超限拦截 | `journalctl -u litellm -u litellm-https -n 50 \| grep 输入过长` |
| 查看 vLLM 请求日志 | `docker logs deepseek-v4-flash-0731-0 --tail 50 \| grep "Received request"` |
| watchdog 日志 | `tail -f ~/logs/vllm-watchdog.log` |
| 暂停自愈(人工维护) | `touch /home/dgxdeploy/.vllm_maintenance`(结束 `rm`) |
| 防火墙状态 | `sudo iptables -L INPUT -n` / `sudo systemctl status vllm-port-lockdown` |
