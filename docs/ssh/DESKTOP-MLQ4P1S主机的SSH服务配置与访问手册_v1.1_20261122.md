# 本机 SSH 服务配置与访问手册（v1.1）

> 适用主机：`DESKTOP-MLQ4P1S`（Windows）
> 编写日期：2026-11-22
> 关联文档：《Node123 SSH 连接说明》（本机访问 Node123 的反向说明）

## 1. 目的与结论

本文说明如何把本机作为 SSH 服务端，供集群其它节点（Node0、Node1、Node123 等）访问，并记录当前配置、验证结果、客户端接入方法和安全注意事项。

截至 2026-11-22 的结论：

- 本机已安装 **OpenSSH Server**，`sshd` 服务处于 **Running / Automatic**。
- 服务监听 **TCP/22**（`0.0.0.0:22` 与 `[::]:22`），Windows 防火墙已有入站放行规则。
- 保留本地管理员账号 **`bot`** 作为唯一 SSH 运维账号，用于其它节点以密码方式登录；临时账号 `winbot` 已删除。
- 已验证以下路径均可登录成功：
  - 本机回环：`127.0.0.1:22`
  - 管理网：`172.19.50.63:22`
  - Tailscale：`100.120.170.99:22`
  - 跨节点实测：Node123（`cube-fe5e`）经管理网和 Tailscale 连续登录各 3 次，全部成功。
- 已验证远程命令执行、用户目录文件写入和 SFTP 子系统可用。
- 已将 `sshd_config` 的 SFTP 子系统从相对路径改为绝对路径，修复了 SFTP 会话立即退出的问题；原配置备份为 `C:\ProgramData\ssh\sshd_config.bak_20261122`。
- **安全提示**：当前仍启用密码认证，且 `bot` 口令强度不足。请尽快修改为强口令，并按第 6.5 节部署公钥后关闭密码认证；不要把 22 端口直接暴露到公网。

## 2. 主机与接入地址

### 2.1 主机信息

| 项目 | 当前值 |
| --- | --- |
| 主机名 | `DESKTOP-MLQ4P1S` |
| 操作系统 | Windows 10.0.26300（AMD64） |
| SSH 服务端 | `OpenSSH_for_Windows_9.5p2` |
| SSH 服务 | `sshd`（Running，Automatic） |
| SSH 端口 | `22/tcp` |
| 默认 Shell | `cmd.exe`（未设置 `HKLM\SOFTWARE\OpenSSH\DefaultShell`） |
| 配置文件 | `C:\ProgramData\ssh\sshd_config` |
| 主机密钥 | `C:\ProgramData\ssh\ssh_host_{rsa,ecdsa,ed25519}_key` |
| 管理员公钥 | `C:\ProgramData\ssh\administrators_authorized_keys` |
| 日志（事件查看器） | `OpenSSH/Operational`、`OpenSSH/Admin` |

### 2.2 接入地址

| 地址 | 网卡 / 通道 | 适用场景 | 说明 |
| --- | --- | --- | --- |
| `127.0.0.1` | 回环 | 本机自检 | 只用于验证服务本身 |
| `172.19.50.63` | WLAN，`172.19.48.0/22` | 同一管理网内的节点 | DHCP 地址，存在漂移可能；失联时先确认当前地址 |
| `100.120.170.99` | Tailscale | 跨网段、远程运维首选 | 不受 WLAN 地址变化影响，推荐写入客户端配置 |
| `192.168.48.1` | Hyper-V 默认交换机 | 仅本机虚拟化环境 | 不建议作为节点访问入口 |
| `198.18.0.1` | CuteCloud 代理虚拟网段 | 代理内部使用 | 不作为 SSH 接入地址 |

> 建议客户端优先使用 Tailscale 地址 `100.120.170.99`；管理网可用时再用 `172.19.50.63` 作为备用。

### 2.3 集群节点对照

| 节点 | 主机名 | Tailscale 地址 | 管理网地址 |
| --- | --- | --- | --- |
| 本机 | `DESKTOP-MLQ4P1S` | `100.120.170.99` | `172.19.50.63` |
| Node0 | `cube-f22b` | `100.67.107.19` | `172.19.50.70` |
| Node1 | `cube-0137` | `100.103.250.108` | `172.19.49.159` |
| Node123 | `cube-fe5e` | `100.79.120.65` | `172.19.51.123` |

## 3. 服务管理

以下命令需在**管理员 PowerShell** 中执行。停止或重启 `sshd` 会中断当前所有 SSH 会话，请保留一个控制台或远程桌面会话作为兜底。

```powershell
# 查看状态
Get-Service sshd

# 启动 / 停止 / 重启
Start-Service sshd
Stop-Service sshd
Restart-Service sshd

# 设置开机自启
Set-Service -Name sshd -StartupType Automatic

# 校验配置语法（修改 sshd_config 后必须执行）
& "$env:ProgramFiles\OpenSSH\sshd.exe" -t

# 查看最终生效的配置
& "$env:ProgramFiles\OpenSSH\sshd.exe" -T
```

安装状态检查（正常情况下应存在 `sshd.exe` 与服务）：

```powershell
Test-Path "$env:ProgramFiles\OpenSSH\sshd.exe"
Get-Service sshd
```

## 4. 当前 sshd 配置说明

`sshd_config` 使用 Windows OpenSSH 默认配置，未做破坏性修改。关键生效项如下：

| 配置项 | 当前值 | 说明 |
| --- | --- | --- |
| `Port` | `22` | 默认 SSH 端口 |
| `ListenAddress` | `0.0.0.0:22`、`[::]:22` | 监听全部 IPv4/IPv6 地址 |
| `PasswordAuthentication` | `yes` | 允许密码登录（当前依赖项） |
| `PubkeyAuthentication` | `yes` | 允许公钥登录 |
| `AuthorizedKeysFile` | `.ssh/authorized_keys` | 普通用户公钥路径 |
| `Match Group administrators` | `AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys` | 管理员组改用集中公钥文件 |
| `Subsystem sftp` | `C:/Program Files/OpenSSH/sftp-server.exe` | SFTP 可用（已改为绝对路径） |
| `AllowTcpForwarding` | `yes` | 允许 SSH 端口转发 |
| `X11Forwarding` | `no` | 不启用 X11 |
| `PermitTunnel` | `no` | 不启用 tun/tap 隧道 |
| `PermitRootLogin` | `without-password` | 不允许空口令/密码方式登录高权限账户 |

配置文件位置：

```text
C:\ProgramData\ssh\sshd_config
```

修改后的标准流程：

```powershell
notepad C:\ProgramData\ssh\sshd_config
& "$env:ProgramFiles\OpenSSH\sshd.exe" -t
Restart-Service sshd
```

### 4.1 SFTP 子系统路径修复

原配置为相对路径：

```text
Subsystem	sftp	sftp-server.exe
```

Windows 服务方式启动时，工作目录不在 `C:\Program Files\OpenSSH`，`sftp-server.exe` 可能无法解析，表现为认证成功后 SFTP 会话立即退出（客户端提示 `Connection closed`）。本次已修改为绝对路径：

```text
Subsystem	sftp	C:/Program Files/OpenSSH/sftp-server.exe
```

原文件备份：`C:\ProgramData\ssh\sshd_config.bak_20261122`。修改后已执行 `sshd -t` 校验并重启服务，SFTP 读写验证通过。

> 不要直接复制 Linux 的 `sshd_config` 覆盖 Windows 版本；Windows 的路径、SFTP 子系统和 `Match Group administrators` 写法不同。

### 4.2 可选：把默认 Shell 改为 PowerShell

当前远程命令默认由 `cmd.exe` 解释。如需默认使用 PowerShell：

```powershell
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
  -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force
Restart-Service sshd
```

> 改完后客户端命令语法要按 PowerShell 编写。若团队脚本大量使用 `cmd` 语法，建议保持现状。

## 5. 账号与权限

### 5.1 账号一览

| 账号 | 类型 | 用途 | 公钥路径 |
| --- | --- | --- | --- |
| `bot` | 本地管理员 | 本机唯一 SSH 运维账号 | `C:\ProgramData\ssh\administrators_authorized_keys` |
| `Administrator` | 内置管理员 | 应急维护、系统级操作 | `C:\ProgramData\ssh\administrators_authorized_keys` |

> 按运维要求，2026-11-22 已删除临时的 `winbot` 账号，仅保留 `bot`。`winbot` 的用户配置文件目录仍被系统占用，已登记 `RunOnce`，将在下次登录时自动清理。

账号口令统一登记在项目根目录 `.env` 的 `LOCAL_SSH_*` 中，不写入本文档，也不提交 Git。

`bot` 当前状态：

- 已启用，属于本地 **Administrators** 组，SSH 密码登录与 SFTP 均已实测可用。
- 可执行系统级查询和管理命令，例如 `Get-Service sshd`、`net session`。
- 用户目录：`C:\Users\bot`。
- 口令由运维单独管理，**不写入本文档或 Git 仓库**；如需落盘，仅写入项目根目录 `.env`（已被 `.gitignore` 忽略）。
- 当前口令强度不足，建议尽快改密并部署公钥登录。

修改口令（在控制台或管理员终端执行，按提示输入两次新口令）：

```powershell
net user bot *
```

建议口令至少 16 位，包含大小写字母、数字和符号，且不与用户名相同。

### 5.2 账号排查

```powershell
Get-LocalUser -Name bot
net user bot
Get-LocalGroupMember -Group Administrators
quser
```

> 本机不再保留 `winbot`。如后续确需新增普通用户，应同时将其加入 `Users` 组，并在变更后重启 `sshd`。新建账号首次登录时若出现“密码认证成功但会话立即关闭”，优先检查账号组归属和用户配置文件是否创建成功。

## 6. 客户端接入

### 6.1 密码登录

在 Node0 / Node1 / Node123 或任意客户端执行：

```bash
# 管理网
ssh bot@172.19.50.63

# Tailscale（推荐）
ssh bot@100.120.170.99

# 备用内置管理员（应急使用）
ssh Administrator@100.120.170.99
```

首次连接会提示确认主机指纹。应在服务端执行以下命令核对，确认一致后再输入 `yes`：

```powershell
ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub
```

### 6.2 客户端 SSH 配置别名

在客户端的 `~/.ssh/config` 中加入：

```sshconfig
Host dgx-win
    HostName 100.120.170.99
    User bot
    ServerAliveInterval 30
    ServerAliveCountMax 6

Host dgx-win-lan
    HostName 172.19.50.63
    User bot
    ServerAliveInterval 30
    ServerAliveCountMax 6
```

之后可直接使用：

```bash
ssh dgx-win
ssh dgx-win-lan
```

### 6.3 远程命令

默认 Shell 为 `cmd.exe`，简单命令示例：

```bash
ssh dgx-win "hostname"
ssh dgx-win "whoami"
ssh dgx-win "echo %USERPROFILE%"
ssh dgx-win "dir C:\\Users\\bot"
```

调用 PowerShell：

```bash
ssh dgx-win "powershell -NoProfile -Command Get-Date"
ssh dgx-win "powershell -NoProfile -Command Get-ChildItem C:\\Users\\bot"

# 命令中包含管道时，用外层单引号保护 PowerShell 的双引号
ssh dgx-win 'powershell -NoProfile -Command "Get-ChildItem C:\Users\bot | Select-Object -First 5"'
```

需要交互式 PowerShell 时，先登录再执行 `powershell`。

> `bot` 属于本地管理员组，可直接查询或管理 `sshd` 等系统服务，例如：
>
> ```bash
> ssh dgx-win 'powershell -NoProfile -Command "Get-Service sshd"'
> ```

### 6.4 文件传输（SFTP / SCP）

```bash
# 交互式 SFTP
sftp dgx-win

# 上传
scp ./local-file.txt dgx-win:C:/Users/bot/

# 下载
scp dgx-win:C:/Users/bot/local-file.txt ./
```

SFTP 登录后的常用命令：`pwd`、`ls`、`put`、`get`、`mkdir`、`rm`、`exit`。

> 建议把文件放在 `C:\Users\bot` 下；向 `C:\ProgramData`、`C:\Windows` 等系统目录写入仍受 UAC 限制，必要时使用提升权限的进程或控制台操作。

### 6.5 部署公钥登录（推荐）

密码认证仅用于过渡。完成以下步骤并验证公钥可用后，建议关闭密码认证。

#### 第一步：在客户端生成专用密钥

每台节点使用独立密钥，不要跨节点复用：

```bash
ssh-keygen -t ed25519 -a 100 -C "cube-fe5e->desktop-mlq4p1s" \
  -f ~/.ssh/desktop_mlq4p1s_ed25519
cat ~/.ssh/desktop_mlq4p1s_ed25519.pub
```

#### 第二步：把公钥写入服务端正确位置

`bot` 属于管理员组，公钥统一写入管理员公钥文件（在服务端管理员 PowerShell 中执行）：

```powershell
$file = 'C:\ProgramData\ssh\administrators_authorized_keys'
$key  = 'ssh-ed25519 AAAA...客户端公钥... cube-fe5e->desktop-mlq4p1s'
Add-Content -Path $file -Value $key -Encoding ascii
icacls $file /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

注意事项：

- 管理员组使用 `C:\ProgramData\ssh\administrators_authorized_keys`，**不是** `C:\Users\bot\.ssh\authorized_keys` 或 `C:\Users\Administrator\.ssh\authorized_keys`。
- `ssh-copy-id` 默认写入用户目录，对 Windows 管理员账号通常无效，请按上面的方式手工写入。
- 公钥文件一行一个公钥，末尾保留换行；不要写入私钥。
- 修改公钥文件不需要重启 `sshd`，下一次认证即生效。

#### 第三步：验证公钥登录

```bash
ssh -i ~/.ssh/desktop_mlq4p1s_ed25519 \
  -o IdentitiesOnly=yes dgx-win "hostname"
```

返回 `DESKTOP-MLQ4P1S` 即成功。

#### 第四步（可选）：关闭密码认证

确认公钥登录可用，并保留一个已登录会话后，在服务端修改：

```text
PasswordAuthentication no
```

然后执行：

```powershell
& "$env:ProgramFiles\OpenSSH\sshd.exe" -t
Restart-Service sshd
```

若配置有误，可从保留的会话或控制台恢复 `PasswordAuthentication yes` 并重启服务。

## 7. 防火墙与网络

### 7.1 现有规则

| 规则名称 | 状态 | 方向 | 动作 | 配置文件 | 端口 |
| --- | --- | --- | --- | --- | --- |
| `OpenSSH Server (sshd)` | 启用 | 入站 | 允许 | Any | TCP/22 |
| `OpenSSH SSH Server Preview (sshd)` | 启用 | 入站 | 允许 | Private | TCP/22 |

查看命令：

```powershell
Get-NetFirewallRule -DisplayName "*SSH*" |
  Select-Object DisplayName,Enabled,Direction,Action,Profile

Get-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" |
  Get-NetFirewallPortFilter
```

### 7.2 新增或收紧规则

若现有规则被误删，可重新创建：

```powershell
New-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" `
  -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any
```

建议把来源限制为管理网和 Tailscale 网段：

```powershell
Set-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" `
  -RemoteAddress 172.19.48.0/22,100.64.0.0/10
```

说明：

- `172.19.48.0/22` 覆盖 Node0/Node1/Node123 当前管理网地址。
- `100.64.0.0/10` 是 Tailscale 使用的 CGNAT 网段。
- 收紧后，其它来源将被拒绝；更改前先确认不会把自己锁在外面。

### 7.3 端口监听检查

```powershell
Get-NetTCPConnection -LocalPort 22 -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

### 7.4 公网暴露原则

- 不要在没有防护的情况下把 TCP/22 映射到公网。
- 跨公网访问优先使用 Tailscale，并在 Tailscale ACL 中限制可访问节点。
- 必须公网暴露时，至少启用仅公钥认证、限制来源 IP、启用登录失败审计与账户锁定，并考虑跳板机/堡垒机。

## 8. 日志与验证

### 8.1 查看登录日志

```powershell
# SSH 服务日志
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 50 |
  Select-Object TimeCreated,Id,Message

# 服务端错误
Get-WinEvent -LogName 'OpenSSH/Admin' -MaxEvents 20 |
  Select-Object TimeCreated,Id,Message
```

Windows 安全日志中与 SSH 登录相关的事件：

| 事件 ID | 含义 |
| --- | --- |
| `4624` | 登录成功（SSH 常见登录类型 3/8） |
| `4625` | 登录失败 |
| `4634` | 注销 |

```powershell
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624,4625} -MaxEvents 50 |
  Where-Object { $_.Message -match 'bot' }
```

### 8.2 一键健康检查脚本

项目已提供：

```text
scripts/check_ssh_server.py
```

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\check_ssh_server.py
.\.venv\Scripts\python.exe scripts\check_ssh_server.py --host 172.19.50.63 --host 100.120.170.99
```

脚本检查服务状态、监听端口、防火墙规则、生效配置、主机密钥、公钥文件和端口横幅，只读不修改系统。退出码 `0` 表示无失败项。

### 8.3 2026-11-22 实测记录

| 检查项 | 命令 / 方式 | 结果 |
| --- | --- | --- |
| 服务状态 | `Get-Service sshd` | Running / Automatic |
| 端口监听 | `Get-NetTCPConnection -LocalPort 22 -State Listen` | `0.0.0.0:22`、`[::]:22` |
| 防火墙 | `Get-NetFirewallRule` | 入站 TCP/22 已放行 |
| 本机回环 | `ssh Administrator@127.0.0.1` | 成功 |
| 管理网地址 | `ssh Administrator@172.19.50.63` | 成功 |
| `bot` 回环 | 密码登录 `127.0.0.1` | 成功 |
| `bot` 管理网 | 密码登录 `172.19.50.63` | 成功 |
| `bot` Tailscale | 密码登录 `100.120.170.99` | 成功 |
| `bot` 权限 | `net session` | `ADMIN=YES` |
| 跨节点登录 | Node123 → 管理网，连续 3 次 | 3/3 成功 |
| 跨节点登录 | Node123 → Tailscale，连续 3 次 | 3/3 成功 |
| 远程写文件 | `echo ... > %USERPROFILE%\ssh_write_test.txt` | 成功，清理完成 |
| SFTP | 读写 `C:\Users\bot` 临时文件 | 成功 |
| SFTP（修复后） | Node123 → Tailscale，上传/下载/删除 | 成功 |
| 临时账号清理 | `Get-LocalUser -Name winbot` | 本地账号已删除；配置文件目录已登记下次登录清理 |
| 健康检查脚本 | `check_ssh_server.py` | 13 项通过，0 失败，0 警告 |

跨节点测试使用的登录账号为 `bot`。此前创建的临时账号 `winbot` 已删除。口令不记录在本文档中。

## 9. 常见故障排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `Connection refused` | 服务未运行、端口未监听、防火墙拦截 | `Get-Service sshd`；`Get-NetTCPConnection -LocalPort 22`；检查防火墙规则 |
| `Connection timed out` | 地址不可达、网段不通、Tailscale 离线 | 先 `ping`/`Test-NetConnection`；`tailscale status`；改用另一地址 |
| `Permission denied (password)` | 账号禁用、口令错误、账号不在允许登录范围 | `Get-LocalUser`；`net user bot`；确认账号已启用 |
| `Permission denied (publickey)` | 公钥写错文件、ACL 不正确、客户端未带对应私钥 | 管理员检查 `administrators_authorized_keys`；普通用户检查 `C:\Users\<用户>\.ssh\authorized_keys`；`ssh -vvv` 查看实际使用的密钥 |
| 密码认证成功但会话立即关闭 / 客户端报 `EOF during negotiation` | 新建账号未加入 `Users` 组、用户配置文件创建异常、组变更未生效 | 检查账号组归属；确认 `C:\Users\<用户名>` 已创建；执行 `Restart-Service sshd` 后重试 |
| SSH 命令正常，但 SFTP 认证后提示 `Connection closed` | `sshd_config` 中 `Subsystem sftp sftp-server.exe` 使用相对路径，服务方式启动时无法解析 | 改为 `Subsystem sftp C:/Program Files/OpenSSH/sftp-server.exe`，`sshd -t` 后重启服务；参见第 4.1 节 |
| `Host key verification failed` | 主机密钥变化或 `known_hosts` 记录过期 | 核对服务端指纹；确认无误后删除客户端对应 `known_hosts` 条目 |
| 修改配置后服务起不来 | `sshd_config` 语法错误 | `sshd -t` 查看行号；恢复上一版配置后重启 |
| 端口被占用 | 其它程序监听 22 | `Get-NetTCPConnection -LocalPort 22` 查 PID；`Get-Process -Id <PID>` |
| 中文乱码 | 客户端与服务端编码不一致 | 临时执行 `chcp 65001`；PowerShell 使用 UTF-8；避免在 `cmd` 中直接输出宽字符 |
| Tailscale 地址不通，但管理网可用 | Tailscale 离线、ACL 限制、Windows 防火墙配置档不匹配 | `tailscale status`、`tailscale ping`；检查防火墙规则的 Profile 是否覆盖 Tailscale（当前为 Any） |
| `ssh-copy-id` 后仍要求密码 | 管理员账号实际读取的是 `administrators_authorized_keys` | 按第 6.5 节手工写入并修正 ACL |

详细调试：

```bash
ssh -vvv bot@100.120.170.99
```

服务端同时观察：

```powershell
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 20 -Follow
```

## 10. 安全基线

1. **立即修改 `bot` 弱口令**，不要使用与用户名相同的口令；该账号是管理员，弱口令风险较高。
2. 完成公钥部署后设置 `PasswordAuthentication no`，优先使用每节点独立密钥。
3. 防火墙来源限制为管理网 `172.19.48.0/22` 与 Tailscale `100.64.0.0/10`，不要直接暴露公网。
4. 坚持最小权限：`bot` 是管理员账号，仅用于必要的运维操作；如后续创建普通账号，应保持标准用户权限。
5. 定期审计公钥：
   - `C:\ProgramData\ssh\administrators_authorized_keys`
   - 删除离职人员、退役节点和过期密钥。
6. 启用并定期检查登录失败事件（`4625`、`OpenSSH/Operational`），配置账户锁定策略。
7. 保护主机私钥 `C:\ProgramData\ssh\ssh_host_*_key`，不要复制外传；更换主机密钥会导致所有客户端告警。
8. 通过 Windows Update 或官方渠道保持 OpenSSH 版本更新。
9. 不要把口令、私钥写入脚本、文档或 Git；统一放在 `.env` 或系统凭据管理器中。
10. 变更 `sshd_config` 前备份，变更后先 `sshd -t` 再重启，并保留一个已登录会话用于回滚。

## 11. 停用与回滚

需要临时关闭 SSH 服务时：

```powershell
Stop-Service sshd
```

需要长期停用：

```powershell
Stop-Service sshd
Set-Service -Name sshd -StartupType Disabled
Disable-NetFirewallRule -DisplayName "OpenSSH Server (sshd)"
```

恢复：

```powershell
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Enable-NetFirewallRule -DisplayName "OpenSSH Server (sshd)"
```

本次已删除临时账号 `winbot`，仅保留 `bot`；其配置文件目录已登记到 `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce`，下次登录时自动清理。如后续创建了其它临时账号并需要清理：

```powershell
Remove-LocalUser -Name <账号名>
```

> 停用服务或删除账号前，务必确认仍保留控制台、远程桌面或其它带外管理通道，避免失联。不要删除当前唯一的运维账号 `bot`。

## 12. 变更记录

| 日期 | 版本 | 变更内容 |
| --- | --- | --- |
| 2026-11-22 | v1.0 | 确认 OpenSSH 服务与防火墙状态；修复 SFTP 子系统绝对路径；完成本机、管理网、Tailscale 及 Node123 跨节点登录、文件写入与 SFTP 验证；新增 `scripts/check_ssh_server.py` 与本手册 |
| 2026-11-22 | v1.1 | 按运维要求删除临时账号 `winbot`，仅保留管理员账号 `bot`；同步更新全部接入示例、验证记录、安全基线与故障排查说明 |

## 附录 A：Linux 节点接入速查

在 Node123 等 Linux 节点上：

```bash
# 管理网登录
ssh bot@172.19.50.63

# Tailscale 登录
ssh bot@100.120.170.99

# 单条命令
ssh bot@100.120.170.99 "hostname"

# 上传文件
scp ./file.tar.gz bot@100.120.170.99:C:/Users/bot/

# 交互式 SFTP
sftp bot@100.120.170.99
```

## 附录 B：服务端快速自检清单

```powershell
Get-Service sshd
Get-NetTCPConnection -LocalPort 22 -State Listen
Get-NetFirewallRule -DisplayName "OpenSSH Server (sshd)"
& "$env:ProgramFiles\OpenSSH\sshd.exe" -t
Get-LocalUser -Name bot
net user bot
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 10
.\.venv\Scripts\python.exe scripts\check_ssh_server.py
```
