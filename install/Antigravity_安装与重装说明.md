# Antigravity 系列应用安装与重装说明

适用主机：本机工作站 `cube-fe5e`（NODE123）；脚本按 aarch64 平台 tar.gz 安装包设计。

本机共有两个**相互独立**的 Antigravity 应用，均由同一脚本管理：

| 应用 | `--app` | 安装包内目录 | 安装路径 | 命令入口 | 当前版本 |
|---|---|---|---|---|---|
| Antigravity IDE | `ide`（默认） | `Antigravity IDE/` | `/opt/antigravity-ide` | `/usr/local/bin/antigravity-ide` | IDE 2.8.0 / VSCode 1.107.0 |
| Antigravity（桌面应用） | `app` | `Antigravity-arm64/` | `/opt/antigravity` | `/usr/local/bin/antigravity` | 44.9.1 |

| 项目 | 内容 |
|---|---|
| 安装脚本 | `install/install_antigravity.py` |
| 默认安装包 | `ide` → `~/Downloads/Antigravity IDE.tar.gz`；`app` → `~/Downloads/Antigravity (1).tar.gz` |
| 桌面项 | `ide` → `~/.local/share/applications/antigravity-ide.desktop`；`app` → `~/.local/share/applications/antigravity.desktop` |
| 用户数据 | `ide` → `~/.antigravity-ide`、`~/.config/Antigravity IDE`；`app` → `~/.config/Antigravity`（重装不清理，保留登录态与设置） |

> 两者共用同一套 Electron 运行时布局，但目录、可执行文件、软链和桌面项完全分离，**升级其中一个不会影响另一个**。
> 另有一个第三方工具 “Antigravity Tools”（`com.lbjlaq.antigravity-tools`），不归本脚本管理。

## 一、快速使用

```bash
cd /home/bot/work/mydgx

# 0) 查看两个应用的安装状态（不修改任何文件）
.venv/bin/python install/install_antigravity.py --app ide --check
.venv/bin/python install/install_antigravity.py --app app --check

# 1) 重装/升级 Antigravity IDE（默认 --app ide，可省略该参数）
.venv/bin/python install/install_antigravity.py

# 2) 重装/升级 Antigravity 桌面应用
.venv/bin/python install/install_antigravity.py --app app

# 3) 指定其它安装包
.venv/bin/python install/install_antigravity.py --app app --tarball "$HOME/Downloads/Antigravity.tar.gz"

# 4) 确认新版稳定后，删除旧版本备份
.venv/bin/python install/install_antigravity.py --app app --purge-old
```

## 二、脚本参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--app {ide,app}` | `ide` | 目标应用：`ide`=Antigravity IDE，`app`=Antigravity 桌面应用 |
| `--tarball` | 按 `--app` 取默认包 | 安装包路径；包内目录名须与该应用的约定一致 |
| `--prefix` | `/opt/antigravity-ide` 或 `/opt/antigravity` | 安装目录 |
| `--sudo-pass` | 读 `.env` | sudo 口令，依次取 `LOCAL_SUDO_PASS` → `NODE123_RDP_PASS` → `SSH_NODE123_PASS`，均未配置时交互输入 |
| `--purge-old` | 关闭 | 重装后删除旧版本备份目录 |
| `--check` | 关闭 | 仅做健康检查，返回码 0 表示正常 |
| `-v/--verbose` | 关闭 | 输出 DEBUG 级日志 |

## 三、执行流程

1. **停止进程**：按应用路径 `pkill -f <prefix>/<binary>`，避免替换文件时被占用。
2. **备份旧版**：旧目录重命名为 `<prefix>-<版本>.bak-<时间戳>`，例如
   `/opt/antigravity-44.4.3.bak-20261120-083504`、`/opt/antigravity-ide-2.5.5.bak-20261120-062313`，默认保留以便回滚。
3. **解包**：只提取包内约定目录到 `~/.cache/ag-*/` 暂存，校验可执行文件存在后整体移动到目标路径。
4. **权限校正**：目录归属 `bot:bot`；`chrome-sandbox` 设为 `root:root 4755`（Electron 沙箱必需，缺失会导致启动失败）。
5. **入口与快捷方式**：重建 `/usr/local/bin` 软链，写入用户级桌面项并刷新菜单缓存（原桌面项存为 `.desktop.bak`）。
6. **健康检查**：`ide` 读 `resources/app/product.json`（`ideVersion`/`version`），`app` 读 `version` 文件；再核验沙箱权限、主程序、软链与桌面项，全部通过才返回 0。

## 四、健康检查输出释义

```text
Antigravity IDE 检查：版本 IDE 2.8.0 / VSCode 1.107.0，sandbox root:root:4755，主程序 存在，命令入口 正常，桌面项 存在 → ✅ 正常
Antigravity 检查：版本 44.9.1，sandbox root:root:4755，主程序 存在，命令入口 正常，桌面项 存在 → ✅ 正常
```

- `sandbox 缺失/权限异常`：执行 `sudo chown root:root <prefix>/chrome-sandbox && sudo chmod 4755 <prefix>/chrome-sandbox`。
- `命令入口 异常`：重跑脚本即可重建软链。
- `桌面项 缺失`：重跑脚本会重新写入用户级 desktop 文件。
- `内置目录缺失`（`安装包内未找到 xxx/ 目录`）：安装包与 `--app` 不匹配，例如把 IDE 包传给 `--app app`。

## 五、启动与验证

```bash
# Antigravity IDE：单实例，二次调用会激活已有窗口
antigravity-ide

# Antigravity 桌面应用
antigravity
```

也可从应用菜单 / 桌面图标点击 “Antigravity IDE” 或 “Antigravity”。

启动日志中以下内容属本机远程桌面、无独显环境的已知噪声，不影响使用：

- `tu_knl.cc ... failed to open device /dev/dri/renderD128 (VK_ERROR_INCOMPATIBLE_DRIVER)`
- `Exiting GPU process due to errors during initialization`
- `SharedImageManager::ProduceSkia: incompatible backing` / `glCopySubTexture: unknown mailbox`
- `APPIMAGE env is not defined, current application is not an AppImage`（tar.gz 安装的正常提示）

判断是否真正启动成功，看进程与窗口（本机未装 wmctrl，用 xwininfo）：

```bash
pgrep -af "/opt/antigravity/antigravity"
xwininfo -root -tree | grep -i antigravity
```

## 六、回滚

```bash
# 以 Antigravity 桌面应用为例：先关闭，再就地换回备份
pkill -f "/opt/antigravity/antigravity"
sudo mv /opt/antigravity /opt/antigravity.new
sudo mv /opt/antigravity-44.4.3.bak-20261120-083504 /opt/antigravity

# IDE 回滚同理，把路径换成 /opt/antigravity-ide 与对应 .bak 目录即可
.venv/bin/python install/install_antigravity.py --app app --check
```

用户数据目录（`~/.config/Antigravity`、`~/.antigravity-ide`）未改动，回滚后配置与插件仍然可用；若新版本曾写高版本格式，回滚后如出现异常可临时改名该目录重建（会丢失本地设置，操作前先备份）。

## 七、注意事项

- **sudo 口令不落盘**：脚本只从 `.env`（已在 `.gitignore` 中）或命令行/交互读取，不得写死在代码里。
- **单实例限制**：同一用户每个应用只能有一个实例，重装前脚本会主动结束旧进程；桌面会话里有未保存文件时请先手动保存。
- **不要急于 `--purge-old`**：确认新版稳定运行几天后再清理，每个旧备份约 500–770 MB。
- **包内目录名固定**：脚本按 `PROFILES` 中的 `member` 精确匹配；官方包目录名变化时需同步调整。
- **系统图标**：`ide` 依赖 `/usr/share/icons/hicolor/1024x1024/apps/antigravity-ide.png`，`app` 依赖 `/usr/share/icons/hicolor/512x512/apps/antigravity.png`；缺失时仅显示通用图标，不影响使用。
- **IDE 与桌面应用不要混装**：两者都用 `antigravity` 前缀命名，务必用 `--app` 区分，避免把桌面应用解包进 `/opt/antigravity-ide`。

## 八、变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-11-20 | v1.1 | 脚本合并为 `install/install_antigravity.py`，支持 `--app ide/app`；本机 Antigravity 由 44.4.3 升级到 44.9.1（旧版备份保留） |
| 2026-11-20 | v1.0 | 首次迁入 `install/` 目录并补充说明；同日用 `Antigravity IDE.tar.gz` 将本机 IDE 由 2.5.5 重装为 2.8.0 |
