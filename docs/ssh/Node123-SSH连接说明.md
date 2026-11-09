# Node123 SSH 连接说明

本文说明本机通过 SSH 访问 Node123 的方式。登录凭证和私钥内容不写入仓库。

## 连接信息

| 项目 | 配置 |
| --- | --- |
| 主机 | `172.19.51.123` |
| SSH 端口 | `22` |
| 用户 | `bot` |
| 本机 SSH 别名 | `node123` |
| 本机私钥 | `~/.ssh/node123_ed25519` |
| 已验证远端主机名 | `cube-fe5e` |

本机已有对应的私钥和 SSH 配置。Windows 上私钥通常位于 `%USERPROFILE%\.ssh\node123_ed25519`，配置文件位于 `%USERPROFILE%\.ssh\config`。该文件应由本机用户管理，不要复制到项目目录或提交到 Git。

## 连接与验证

在 PowerShell 或其他终端运行：

```powershell
ssh node123
```

运行单条远端命令：

```powershell
ssh node123 "hostname"
```

非交互连接检查：

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=8 node123 "hostname"
```

本机也配置了按 IP 连接的入口：

```powershell
ssh bot@172.19.51.123
```

连接成功时，主机名应为 `cube-fe5e`。如果本机 SSH 配置尚未包含 `node123`，可在用户级 `~/.ssh/config` 添加：

```sshconfig
Host node123
    HostName 172.19.51.123
    User bot
    IdentityFile ~/.ssh/node123_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 6
```

## 密钥安全

- 私钥 `node123_ed25519` 只保存在本机 SSH 目录；不要提交、上传或通过聊天发送。
- `.pub` 后缀文件是公钥。只有公钥需要部署到远端账号的 `~/.ssh/authorized_keys`，不要把私钥内容放到远端。
- 登录密码或密钥口令不写入本说明、命令行参数或仓库文件。密码不能转换为 SSH 私钥。
- 在另一台客户端使用密钥认证时，需要由管理员将该客户端新生成的公钥加入远端 `authorized_keys`；不要通过复制本说明来传递私钥。

## 排错

确认 SSH 客户端实际读取的配置：

```powershell
ssh -G node123 | Select-String '^(hostname|user|identityfile|port) '
```

查看详细连接过程：

```powershell
ssh -vvv node123
```

若出现 `Permission denied (publickey)`，检查 `IdentityFile` 指向的私钥是否存在、用户名是否为 `bot`，以及对应公钥是否已加入远端 `~/.ssh/authorized_keys`。若连接超时，先确认本机网络可访问 `172.19.51.123:22`。
