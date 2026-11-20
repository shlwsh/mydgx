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

# 4) 仅修复图标/桌面项（图标变灰、Dock 空白时用；不重新解包）
.venv/bin/python install/install_antigravity.py --app ide --icons-only

# 5) 确认新版稳定后，删除旧版本备份
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
| `--icons-only` | 关闭 | 只重装图标与桌面项，不重新解包（图标丢失后一键修复） |
| `--check` | 关闭 | 仅做健康检查，返回码 0 表示正常 |
| `-v/--verbose` | 关闭 | 输出 DEBUG 级日志 |

## 三、执行流程

1. **停止进程**：按应用路径 `pkill -f <prefix>/<binary>`，避免替换文件时被占用。
2. **备份旧版**：旧目录重命名为 `<prefix>-<版本>.bak-<时间戳>`，例如
   `/opt/antigravity-44.4.3.bak-20261120-083504`、`/opt/antigravity-ide-2.5.5.bak-20261120-062313`，默认保留以便回滚。
3. **解包**：只提取包内约定目录到 `~/.cache/ag-*/` 暂存，校验可执行文件存在后整体移动到目标路径。
4. **权限校正**：目录归属 `bot:bot`；`chrome-sandbox` 设为 `root:root 4755`（Electron 沙箱必需，缺失会导致启动失败）。
5. **图标安装**：从 `install/assets/` 的母版 PNG 生成 512/256/128/64/48/32 六种尺寸，装入 `/usr/share/icons/hicolor/<尺寸>/apps/`，再执行 `gtk-update-icon-cache`。
6. **入口与快捷方式**：重建 `/usr/local/bin` 软链，写入用户级桌面项并刷新菜单缓存（原桌面项存为 `.desktop.bak`）。
7. **健康检查**：`ide` 读 `resources/app/product.json`（`ideVersion`/`version`），`app` 读 `version` 文件；再核验沙箱权限、主程序、软链、桌面项与图标，全部通过才返回 0。

## 四、健康检查输出释义

```text
Antigravity IDE 检查：版本 IDE 2.8.0 / VSCode 1.107.0，sandbox root:root:4755，主程序 存在，命令入口 正常，桌面项 存在，图标 存在 → ✅ 正常
Antigravity 检查：版本 44.9.1，sandbox root:root:4755，主程序 存在，命令入口 正常，桌面项 存在，图标 存在 → ✅ 正常
```

- `sandbox 缺失/权限异常`：执行 `sudo chown root:root <prefix>/chrome-sandbox && sudo chmod 4755 <prefix>/chrome-sandbox`。
- `命令入口 异常`：重跑脚本即可重建软链。
- `桌面项 缺失`：重跑脚本会重新写入用户级 desktop 文件。
- `图标 缺失`：执行 `--icons-only` 重新生成并刷新图标缓存。
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

## 六、图标与“应用面板看不到”两处坑

> 与具体应用无关的通用排查方法（图标主题索引、WM_CLASS 关联、多尺寸生成）见
> `docs/工作站桌面图标与启动器修复方案_v1.0_20261120.md`，并可用
> `scripts/check_desktop_integration.py` 一键自检。

### 6.1 图标变成灰色通用图标

本机 `hicolor/index.theme` 的 `Directories` 列表**没有登记 `1024x1024`**（`grep -c 1024x1024 /usr/share/icons/hicolor/index.theme` 结果为 0）。若图标只放在 `1024x1024/apps/`，GTK/GNOME 按索引查不到该图标名，Dock 与桌面项就退回灰色通用图标——即使 PNG 文件明明存在。

脚本的做法：用 `install/assets/` 里的母版图生成 512/256/128/64/48/32 六档，写入已登记的尺寸目录并刷新缓存，因此重装后不会再出现“没有图标”。

自检命令：

```bash
# 图标主题能否查到（应输出具体 png 路径，而不是“未找到”）
python3 - <<'EOF'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
theme = Gtk.IconTheme.get_default()
for name in ("antigravity-ide", "antigravity"):
    info = theme.lookup_icon(name, 64, 0)
    print(f"{name}: {info.get_filename() if info else '❌ 未找到'}")
EOF
```

### 6.2 应用面板（Show Applications）里找不到

这是 GNOME Shell 的既定行为，源码 `appDisplay.js` 的 `_loadApps()`：

```js
this._appInfoList = Shell.AppSystem.get_default().get_installed().filter(appInfo =>
    !this._appFavorites.isFavorite(appInfo.get_id()) && ...);
```

**已固定到 Dock 的应用会被有意从应用面板排除**，一个应用只能在“Dock 固定”与“面板列出”之间二选一。本机把两个 Antigravity 都固定到了 Dock，所以面板里看不到；但：

- Dock 图标可见、可直接点击启动（图标修复后已正常）；
- GNOME 概览里输入 `antigravity` 搜索仍能命中并启动。

如需让其在应用面板出现，可取消收藏（Dock 上也会随之消失）；最省事的做法是在应用面板里右键该应用选择“取消固定”，或用下面的命令：

```bash
# 查看当前收藏
gsettings get org.gnome.shell favorite-apps

# 移除指定项（其余收藏保持不变）
python3 - <<'EOF'
import ast, subprocess
key = "org.gnome.shell favorite-apps"
current = ast.literal_eval(subprocess.check_output(["gsettings", "get", key], text=True))
drop = {"antigravity.desktop", "antigravity-ide.desktop"}
kept = [a for a in current if a not in drop]
subprocess.run(["gsettings", "set", key, "[" + ", ".join(f"'{a}'" for a in kept) + "]"], check=True)
print("已移除：", sorted(set(current) & drop))
EOF
```

## 七、回滚

```bash
# 以 Antigravity 桌面应用为例：先关闭，再就地换回备份
pkill -f "/opt/antigravity/antigravity"
sudo mv /opt/antigravity /opt/antigravity.new
sudo mv /opt/antigravity-44.4.3.bak-20261120-083504 /opt/antigravity

# IDE 回滚同理，把路径换成 /opt/antigravity-ide 与对应 .bak 目录即可
.venv/bin/python install/install_antigravity.py --app app --check
```

用户数据目录（`~/.config/Antigravity`、`~/.antigravity-ide`）未改动，回滚后配置与插件仍然可用；若新版本曾写高版本格式，回滚后如出现异常可临时改名该目录重建（会丢失本地设置，操作前先备份）。

## 八、注意事项

- **sudo 口令不落盘**：脚本只从 `.env`（已在 `.gitignore` 中）或命令行/交互读取，不得写死在代码里。
- **单实例限制**：同一用户每个应用只能有一个实例，重装前脚本会主动结束旧进程；桌面会话里有未保存文件时请先手动保存。
- **不要急于 `--purge-old`**：确认新版稳定运行几天后再清理，每个旧备份约 500–770 MB。
- **包内目录名固定**：脚本按 `PROFILES` 中的 `member` 精确匹配；官方包目录名变化时需同步调整。
- **图标尺寸**：必须落在 `hicolor/index.theme` 已登记的尺寸目录里（本机最大 512），只放 1024 等于没有图标；母版图保存在 `install/assets/`，不要删。
- **图标需要刷新缓存**：手工替换图标后执行 `sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor`，否则 Dock 仍显示旧图标。
- **IDE 与桌面应用不要混装**：两者都用 `antigravity` 前缀命名，务必用 `--app` 区分，避免把桌面应用解包进 `/opt/antigravity-ide`。

## 九、变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-11-20 | v1.2 | 新增图标多尺寸生成与 `--icons-only`；查明“无图标”为 hicolor 未登记 1024x1024 所致，“面板看不到”为 GNOME 对 Dock 收藏应用的设计性排除，补充说明文档 |
| 2026-11-20 | v1.1 | 脚本合并为 `install/install_antigravity.py`，支持 `--app ide/app`；本机 Antigravity 由 44.4.3 升级到 44.9.1（旧版备份保留） |
| 2026-11-20 | v1.0 | 首次迁入 `install/` 目录并补充说明；同日用 `Antigravity IDE.tar.gz` 将本机 IDE 由 2.5.5 重装为 2.8.0 |
