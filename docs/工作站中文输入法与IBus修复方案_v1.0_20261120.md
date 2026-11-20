# 工作站中文输入法（IBus）修复方案 v1.0

适用主机：本机工作站 `cube-fe5e`（NODE123，Ubuntu 24.04 + GNOME 46，X11 会话 `:10.0`）
适用现象：打不出中文 / 英文被转成汉字 / `Ctrl+Space` 没反应 / 候选框或托盘图标缺失 / 某些应用里输入法失效。

## 一、本机现状（2026-11-20 实测）

| 项目 | 实际值 |
|---|---|
| IBus 版本 | 1.5.29-rc2（`ibus` 包 1.5.29-2） |
| 会话 | `DISPLAY=:10.0`，`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` |
| 守护进程 | `ibus-daemon --panel disable --xim`（本用户 pid 1922146） |
| 面板模式 | **`--panel disable` 属正常**：GNOME 集成模式下候选框/状态由 gnome-shell 顶部栏提供 |
| 环境变量 | `GTK_IM_MODULE=ibus`、`QT_IM_MODULE=ibus`、`XMODIFIERS=@im=ibus` |
| XIM | `xprop -root XIM_SERVERS` → `@server=ibus`（老式 X 程序可用） |
| 输入源 | `[('xkb','us'), ('ibus','libpinyin'), ('ibus','table:wubi-jidian86')]` |
| 预加载引擎 | `['libpinyin', 'table:wubi-jidian86']` |
| 当前引擎 | `table:wubi-jidian86`（极点五笔 86 版） |
| 切换快捷键 | `Super+Space` / `Ctrl+Space`（后者为本次补绑） |
| 已装引擎包 | `ibus-libpinyin 1.15.7`、`ibus-table-wubi 1.8.12`、`ibus-table-cangjie*`、`ibus-mozc`、`ibus-hangul` |
| 中文字体 | `fc-match "Sans:lang=zh-cn"` → **Noto Sans CJK SC**，共 51 个 zh 字体，候选字不会出方块 |
| `im-config` | 未安装；本机输入法由 GNOME 设置 + IBus 直接管理，无需 `im-config`/`~/.xinputrc` |

> `ps` 里能看到两个 `ibus-daemon`：一个是本用户会话（`:10.0`），另一个属于 **`gdm` 登录界面会话**，属正常现象，**不要 kill 别的用户的 daemon**。

## 二、症状 → 根因 → 处置

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| 1 | 输入英文变成汉字（如敲 `lg` 出“不.五彩和…”） | 当前引擎是五笔/拼音且处于**中文模式**，ASCII 直接被当成编码 | 引擎内按 `Shift` 切英文；或 `Ctrl+Space` 切到 `English (US)`；或命令行 `ibus engine xkb:us::eng` |
| 2 | `Ctrl+Space` 切换输入法没反应 | GNOME 默认只绑定 `Super+Space`，`Ctrl+Space` 空着 | 已补绑（见第三节），验证后立即生效 |
| 3 | 顶部栏没有输入法图标 / 没有候选框 | `ibus-daemon` 以 `--panel disable` 启动时，面板由 gnome-shell 提供；若顶部确实无“中/英”指示，说明 shell 或集成异常 | 正常情况无需处理；异常时重启会话（注销重登）后复查 `ibus engine` |
| 4 | 终端/编辑器里打不出中文 | 应用进程拿不到 IM 环境变量（SSH 启动、systemd 服务、xrdp 新会话等） | 在 `~/.profile` 或 `~/.xprofile` 补三行环境变量后重新登录 |
| 5 | 候选字显示方块 | 缺中文字体或字体缺字形 | 安装 Noto CJK（本机已具备）；可用 `scripts/fix_chinese_fonts.py` 复查 |
| 6 | 输入源列表里没有五笔/拼音 | 引擎包未装或 `preload-engines` 丢失 | `dpkg -l | grep ibus` 确认包；`gsettings get org.freedesktop.ibus.general preload-engines` |
| 7 | 切换后状态混乱、候选框重复 | 历史会话残留 daemon 或配置漂移 | `ibus restart`；仍异常则注销重登 |
| 8 | 装了 fcitx 后输入法全乱 | ibus 与 fcitx 同时占用 `GTK_IM_MODULE` | 只保留一套（本机为 IBus），不要混用 |

## 三、本次已执行的修复（可复现命令）

```bash
# 1) 补绑 Ctrl+Space / Ctrl+Shift+Space（GNOME 默认只有 Super+Space）
gsettings set org.gnome.desktop.wm.keybindings switch-input-source \
  "['<Super>space', 'XF86Keyboard', '<Control>space']"
gsettings set org.gnome.desktop.wm.keybindings switch-input-source-backward \
  "['<Shift><Super>space', '<Shift>XF86Keyboard', '<Shift><Control>space']"

# 2) 让 GNOME 记录的当前输入源与日常使用的五笔引擎一致（仅影响状态显示）
gsettings set org.gnome.desktop.input-sources current 2
```

实测结果：

```text
修复前：Super+Space 可切换；Ctrl+Space 无任何反应
修复后：Ctrl+Space 在 table:wubi-jidian86 ⇄ libpinyin 之间切换
        ibus engine xkb:us::eng 可切到英文，再切回五笔正常
```

> 说明：本次实测中顶部指示器未随引擎实时变化（切到 `xkb:us::eng` 仍显示“中”），存在刷新延迟。**判断输入法真实状态以 `ibus engine` 输出为准**，不要只看图标。

## 四、常用操作速查

| 目的 | 操作 / 命令 |
|---|---|
| 切换输入源 | `Ctrl+Space`（或 `Super+Space`） |
| 反向切换 | `Ctrl+Shift+Space`（或 `Shift+Super+Space`） |
| 切到英文 | `ibus engine xkb:us::eng` |
| 切到五笔 86 | `ibus engine table:wubi-jidian86` |
| 切到拼音 | `ibus engine libpinyin` |
| 查看当前引擎 | `ibus engine` |
| 列出可用引擎 | `ibus list-engine \| grep -E "wubi\|pinyin"` |
| 重启输入法 | `ibus restart`（配置不丢，当前正在输入的编码会中断） |
| 查看输入源顺序 | `gsettings get org.gnome.desktop.input-sources sources` |
| 调整输入源顺序 | `gsettings set org.gnome.desktop.input-sources sources "[('xkb','us'),('ibus','libpinyin'),('ibus','table:wubi-jidian86')]"` |

## 五、诊断命令清单

```bash
# 1) 基础状态：引擎、daemon、环境变量
ibus engine
pgrep -af ibus-daemon
echo "GTK_IM_MODULE=$GTK_IM_MODULE QT_IM_MODULE=$QT_IM_MODULE XMODIFIERS=$XMODIFIERS"

# 2) 会话是否与当前终端一致（多会话机器务必核对）
pid=$(pgrep -u "$USER" -f "ibus-daemon" | head -1)
tr '\0' '\n' < "/proc/$pid/environ" | grep -E "^DISPLAY=|^DBUS_SESSION_BUS_ADDRESS="

# 3) XIM 是否为老式 X 程序提供输入
xprop -root XIM_SERVERS

# 4) 输入源与引擎清单
gsettings get org.gnome.desktop.input-sources sources
gsettings get org.freedesktop.ibus.general preload-engines
ibus list-engine | grep -i -E "wubi|pinyin|table"

# 5) 快捷键是否生效
gsettings get org.gnome.desktop.wm.keybindings switch-input-source
gsettings get org.gnome.desktop.wm.keybindings switch-input-source-backward

# 6) 候选字字体是否会出方块
fc-match "Sans:lang=zh-cn"
fc-list :lang=zh | wc -l

# 7) 端到端验证：切引擎后立即回读
ibus engine xkb:us::eng && ibus engine table:wubi-jidian86 && ibus engine
```

环境变量补齐（仅当第 4 类症状出现时）：

```bash
grep -q "GTK_IM_MODULE" ~/.profile || cat >> ~/.profile <<'EOF'

# IBus 输入法环境变量（GTK/Qt/X11 应用）
export GTK_IM_MODULE=ibus
export QT_IM_MODULE=ibus
export XMODIFIERS=@im=ibus
EOF
```

## 六、故障案例复盘

### 6.1 ASCII 被五笔吞掉（本次工作中实际遇到）

- **现象**：在 GNOME 的 Looking Glass 评估框里输入 `imports.gi.Gio...`，回显变成 `imports.不.G谈.A之I层数.五彩和_荔()...`。
- **根因**：当前引擎为 `table:wubi-jidian86` 且中文模式生效，字母被当五笔编码翻译。属预期行为，不是程序缺陷。
- **处置**：临时 `ibus engine xkb:us::eng` 或 `Ctrl+Space` 切英文；操作结束后切回五笔。
- **启示**：脚本/自动化里模拟键入前应先确认 `ibus engine`，必要时临时切英文再还原。

### 6.2 `Ctrl+Space` 无效

- **现象**：用户按 `Ctrl+Space` 无反应，误以为输入法坏了。
- **根因**：GNOME 46 默认只绑定 `Super+Space` / `XF86Keyboard`；`ibus engine` 与守护进程均正常。
- **处置**：补绑 `Ctrl+Space`（第三节命令），实测切换正常。
- **启示**：报修“输入法不能用”时，先跑 `ibus engine` 判断是真故障还是快捷键差异。

## 七、注意事项

- **不要 kill `gdm` 的 ibus-daemon**：多会话/登录界面各有一个，属正常。
- **不要同时启用 fcitx**：`GTK_IM_MODULE`/`QT_IM_MODULE` 只能指向一套输入法框架；本机统一用 IBus。
- **GNOME 下 `--panel disable` 是正常的**：不要手工再起一个带面板的 `ibus-daemon`，否则会出现两个候选框。
- **`ibus restart` 会短暂中断输入**：适合修复状态卡死/引擎列表异常；配置保存在 dconf（`org.freedesktop.ibus.*`）不会丢。
- **远程桌面（xrdp）会话**：新会话必须继承 `XMODIFIERS`/`QDBUS`/`DBUS_SESSION_BUS_ADDRESS`，否则打不出中文；本机 RDP 方案见 `docs/工作站远程桌面连接与运维使用手册_v4`。
- **snap 应用**（如 Chromium snap）使用独立的 GTK 运行时，个别版本对 IBus 支持不完整，属应用侧限制。
- **字体与输入法分开排查**：能打拼音但候选框是方块 = 字体问题（`fix_chinese_fonts.py`）；完全无候选框 = 引擎/面板问题（`ibus list-engine`、`ibus engine`）。

## 八、变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-11-20 | v1.0 | 首次成文：盘点本机 IBus 现状（版本、引擎、输入源、环境变量、XIM、字体）；补绑 `Ctrl+Space`/`Ctrl+Shift+Space` 并实测通过；整理症状对照表、诊断命令与两个实际案例 |
