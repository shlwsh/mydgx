# 故障处理记录：shl-work 向日葵（AweSun）无法连接排查

> 日期：2026-11-21
> 故障节点：`shl-work`（Windows 笔记本，Wi‑Fi 接入）
> 关联地址：Tailscale `100.100.194.125`、主机名 `shl-work.tailde3d40.ts.net`
> 操作端：`DESKTOP-MLQ4P1S`（WLAN `172.19.50.63/22`，网关 `172.19.51.254`）
> 状态：**未完成修复，阻塞于目标机整机离线**

---

## 1. 故障现象

- 用户反馈 `shl-work` 的 AweSun（向日葵）远程控制服务“不能正常连接”。
- 该机同时是 Tailscale 节点，预期可通过 `100.100.194.125` 运维。
- 现场信息：`shl-work` 为**通过无线连接的笔记本**，登录账号 `administrator`（口令仅保存在 `.env`/现场，不写入本文档）。

## 2. 排查结论（摘要）

**不是单纯的向日葵服务故障，而是 `shl-work` 整机已不在线。**

- Tailscale 显示该节点 `offline`，最后在线时间 `2026-11-20 21:54:52`（本地时间，约 8 小时前）。
- 同一内网范围内（172.16/17/19/21 网段）通过 ARP、NetBIOS、端口、DNS 多种方式均未发现该主机。
- 本机向日葵服务本身正常，且在 `2026-11-20 21:55` 还有一次入站远控会话结束，时间与该节点掉线高度吻合。
- 结论：目标机大概率**已休眠/关机/离开网络**，远程无法唤醒，需要现场唤醒后才能继续修复 AweSun。

## 3. 环境信息

| 项目 | 值 |
|---|---|
| 操作端主机名 | `DESKTOP-MLQ4P1S` |
| 操作端 WLAN | `172.19.50.63/22` |
| 默认网关 / DHCP | `172.19.51.254`（华为设备，开放 22/23 管理口） |
| 企业 DNS | `172.16.0.14`（shwindns）、`172.17.1.14`（hfwindns） |
| 本机代理 | `CuteCloud` TUN（`198.18.0.1/30`，fake‑ip `198.18.x.x`） |
| 本机 Tailscale | `100.120.170.99`，在线 |
| 目标机 Tailscale | `100.100.194.125`，**offline** |

## 4. 排查过程与结果

### 4.1 Tailscale 侧

```powershell
tailscale status
tailscale ping --timeout 5s --c 3 100.100.194.125
```

结果：

- `100.100.194.125 shl-work windows offline, last seen 8h ago, tx ... rx 0`
- `tailscale ping` 3 次全部超时，无任何握手。
- 其他节点（`cube-f22b`、`cube-0137`、`vm-0-3-ubuntu` 等）均在线，排除本机 Tailscale 整体异常。

### 4.2 目标端口连通性

```powershell
Test-NetConnection 100.100.194.125 -Port 22
Test-NetConnection 100.100.194.125 -Port 3389
Test-NetConnection 100.100.194.125 -Port 445
```

结果：端口 22 / 3389 / 445 全部不可达（错误码 `10035`）。

### 4.3 同内网定位（绕开本机代理）

本机 `CuteCloud` TUN 会劫持流量并返回 `198.18.x.x` 伪 IP，因此内网扫描统一从同网段跳板 `cube-f22b`（Tailscale `100.67.107.19`，WLAN `172.19.50.70`）发起：

1. **ARP + UDP 探测**：对 `172.19.48.0/22` 全段触发 ARP，在线主机仅 6 台：

   ```
   172.19.49.46   5c:61:99:4b:66:2b
   172.19.49.50   34:02:86:5b:df:29   (LUCKISSQQ)
   172.19.49.159  f0:68:e3:85:79:54
   172.19.50.63   e4:0d:36:e3:25:0b   (本机)
   172.19.51.11   5c:ba:ef:c1:61:65
   172.19.51.254  c8:9f:1a:56:af:06   (网关)
   ```

2. **端口扫描**：`172.19.48.0/22` 上开放 3389/5985/22 的主机仅有：
   - `172.19.49.50`（NetBIOS 名 `LUCKISSQQ`，非目标机）
   - `172.19.50.63`（本机）
   - `172.19.50.70`（跳板 cube-f22b）
   - `172.19.51.254:22`（网关）

3. **企业 DNS 查询**：

   ```powershell
   nslookup shl-work 172.16.0.14
   nslookup shl-work 172.17.1.14
   ```

   结果：两台 DNS 均返回 `Non-existent domain`，没有 `shl-work` 的 A 记录。
   （直连企业 DNS 需临时添加 `172.16.0.0/12`、`172.19.0.0/16` 经由 `172.19.51.254` 的路由，排查结束后已删除，恢复原状。）

4. **NetBIOS 名称点名（UDP 137 NBSTAT）**：对 `172.16.0.0/16`、`172.17.0.0/16`、`172.19.0.0/16`、`172.21.0.0/16` 扫描，发现的 Windows 主机名中**没有 `SHL-WORK`**，例如：
   - `172.16.0.18 SHEDMS`
   - `172.16.0.173 SH-ECO-SVR1`
   - `172.16.7.30 WINNING-SVR`
   - `172.19.21.146 DESKTOP-RU647OF`
   - `172.21.0.11 WINSVR1`

5. **网关探测**：`172.19.51.254` 仅开放 22/23，Telnet 返回华为风格提示（`Warning: Telnet is not a secure protocol... Stelnet`），无 SNMP 响应；因无管理凭据，无法进一步读取无线客户端表/DHCP 租约。

### 4.4 本机向日葵状态

```powershell
Get-Service AweSunService,SunloginService
Get-Process AweSun
```

结果：

- `AweSunService`：Running / Auto
- `SunloginService`：Stopped / Manual（旧服务，属正常）
- `AweSun.exe` 服务进程、watch、desktopagent 均在运行
- 本机 `config.ini` 中 `hostname=DESKTOP-MLQ4P1S`，登录状态正常
- 日志 `desktop.agent.20261120-215510.log` 显示：`21:55:10` 建立入站远控会话，`21:55:29` 客户端断开；与 `shl-work` 最后在线时间吻合

> 说明：本机 `DESKTOP-MLQ4P1S` 与 `shl-work` 是两台不同机器，本机向日葵工作正常。

## 5. 根因判断

按现有证据，问题不在 AweSun 服务本身，而是：

1. `shl-work` 作为 Wi‑Fi 笔记本，**已进入休眠/关机或脱离网络**；
2. 因此 Tailscale 与向日葵同时掉线；
3. 该类设备在休眠/关机状态下**无法通过普通网络远程唤醒**，需要现场操作或 AP/网卡支持 WoWLAN。

## 6. 后续处理方案

### 6.1 首选：现场唤醒（最快）

1. 到现场开盖/接电源唤醒 `shl-work`；
2. 确认 Windows 已联网，Tailscale 恢复 `online`；
3. 修改电源计划，避免再次失联：
   - 接通电源时“永不睡眠”
   - 合盖行为改为“不采取任何操作”（至少接通电源时）
4. 唤醒后立即执行 AweSun 修复检查（见 6.4）。

### 6.2 可选：通过网关定位/唤醒

若能提供网关 `172.19.51.254` 的管理账号：

1. 查看无线客户端表 / DHCP 租约，确认 `shl-work` 的 MAC 与最后租约；
2. 若 AP 与网卡支持 WoWLAN，可尝试发送魔术包唤醒；
3. 成功率取决于是否启用“网络唤醒”及 AP 是否允许无线唤醒。

### 6.3 可选：向日葵设备直连

本机 AweSun 已保存以下历史远控设备 ID（`1684547786` 为本机，排除）：

- `1646450747`
- `1689707459`
- `628485082`
- `k1904902629`

若其中某台就是 `shl-work`，且其向日葵仍在线（仅 Tailscale 掉线），可从本机向日葵直接连接后进行修复。需要用户确认设备 ID 对应关系。

### 6.4 上线后的 AweSun 修复清单

1. 检查并重启服务与守护进程：
   - `AweSunService`
   - `awesun_guard` / `AweSun.exe --mod=watch`
2. 验证域名解析与 443 连通：
   - `slapi-biz.oray.net`
   - `ws-adv02.sunlogin.oray.com`
   - `api-adv.sunlogin.oray.com`
   - `sunlogin.oray.com`
3. 排查代理/TUN 冲突（Clash、CuteCloud 等）是否劫持了上述域名或 `198.18.x.x` 伪 IP；
4. 查看 `%APPDATA%\Oray\AweSun\log` 最近日志，定位连接失败原因；
5. 检查防火墙出站规则、系统时间、账号登录状态；
6. 必要时重装/升级 AweSun 并重新登录账号；
7. 修复后从另一台设备发起一次远控验证。

## 7. 临时变更与回滚

- 排查期间曾临时添加以下路由以绕开本机 TUN，直接访问企业内网与 DNS：

  ```powershell
  route add 172.16.0.0 mask 255.240.0.0 172.19.51.254 metric 1
  route add 172.19.0.0 mask 255.255.0.0 172.19.51.254 metric 1
  ```

- 排查结束后已删除，路由表恢复原状，未修改目标机及网关配置。

## 8. 待办

- [ ] 确认 `shl-work` 当前物理状态（休眠/关机/离线）
- [ ] 现场唤醒并恢复 Tailscale 在线
- [ ] 确认是否需要提供网关管理凭据以尝试 WoWLAN
- [ ] 确认向日葵历史设备 ID 与 `shl-work` 的对应关系
- [ ] 目标机上线后执行 6.4 修复清单并复测
