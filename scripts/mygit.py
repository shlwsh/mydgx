#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mygit — 一键 git 提交与推送工具（纯 Python 实现，零外部依赖）

用法:
    python scripts/mygit.py [选项] [提交信息...]
    # 或通过虚拟环境：
    .venv\\Scripts\\python scripts/mygit.py [选项] [提交信息...]

选项:
    -m, --message <msg>   指定提交信息（也可直接跟在命令末尾）
    -n, --dry-run         只预览执行流程，不进行实质写操作
    --no-push             只提交，不执行 git push
    --pull                提交后、推送前先执行 git pull --rebase --autostash
    --amend               修正上一次提交（推送时自动附加 --force-with-lease）
    --tag <name>          提交后创建附注标签并一并推送该标签
    -h, --help            显示帮助信息

示例:
    python scripts/mygit.py
    python scripts/mygit.py "docs: 更新项目规则与部署文档"
    python scripts/mygit.py -m "fix: 修复远程连接参数" --dry-run
"""

import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime

# ---------------------------------------------------------------- 路径与终端色彩

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IS_TTY = sys.stdout.isatty() and not os.getenv("NO_COLOR")

C_RESET = "\033[0m" if IS_TTY else ""
C_DIM = "\033[2m" if IS_TTY else ""
C_BOLD = "\033[1m" if IS_TTY else ""
C_RED = "\033[31m" if IS_TTY else ""
C_GREEN = "\033[32m" if IS_TTY else ""
C_YELLOW = "\033[33m" if IS_TTY else ""
C_CYAN = "\033[36m" if IS_TTY else ""


def log(msg=""):
    print(msg, flush=True)


def info(msg):
    log(f"{C_CYAN}[mygit]{C_RESET} {msg}")


def ok(msg):
    log(f"{C_GREEN}[ok]{C_RESET} {msg}")


def warn(msg):
    log(f"{C_YELLOW}[warn]{C_RESET} {msg}")


def fail(msg):
    print(f"{C_RED}[error]{C_RESET} {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 自动激活/切换 .venv


def ensure_venv():
    """如果在根目录下发现 .venv，且当前不是该虚拟环境的解释器，则无缝切换。"""
    if sys.platform == "win32":
        venv_py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    else:
        venv_py = os.path.join(ROOT, ".venv", "bin", "python")

    if os.path.isfile(venv_py):
        curr_py = os.path.realpath(sys.executable)
        target_py = os.path.realpath(venv_py)
        if curr_py != target_py and not os.getenv("MYGIT_IN_VENV"):
            env = os.environ.copy()
            env["MYGIT_IN_VENV"] = "1"
            rc = subprocess.call([venv_py, os.path.abspath(__file__)] + sys.argv[1:], cwd=ROOT, env=env)
            sys.exit(rc)


# ---------------------------------------------------------------- Git 封装


DRY_RUN = False


def git(args, capture=False):
    """
    执行 git 命令。
    使用列表传参，杜绝 shell 拼接中的中文乱码及引号转义风险。
    """
    cmd = ["git", "-c", "core.quotepath=false"] + args
    stdout_mode = subprocess.PIPE if capture else None
    stderr_mode = subprocess.PIPE if capture else None
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            stdout=stdout_mode,
            stderr=stderr_mode,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "code": proc.returncode,
            "stdout": (proc.stdout or "").rstrip(),
            "stderr": (proc.stderr or "").rstrip(),
        }
    except FileNotFoundError:
        fail("未找到 git 可执行程序，请检查系统 PATH。")
        sys.exit(1)


def run_cmd(args, capture=False, probe=False):
    """运行或预览 git 命令。"""
    pretty = f"git {' '.join(args)}"
    if DRY_RUN and not capture and not probe:
        log(f"{C_DIM}(dry-run) {pretty}{C_RESET}")
        return {"code": 0, "stdout": "", "stderr": ""}
    r = git(args, capture=capture or probe)
    if (capture or probe) and os.getenv("MYGIT_VERBOSE"):
        log(f"{C_DIM}> {pretty}{C_RESET}")
    return r


def run_captured(args):
    """执行并同时回显输出。"""
    r = git(args, capture=True)
    if r["stdout"]:
        log(r["stdout"])
    if r["stderr"]:
        log(r["stderr"])
    return r


# ---------------------------------------------------------------- 字符宽度与状态排版

STATUS_LABEL = {
    "??": "新增(未跟踪)",
    "A": "新增",
    "M": "修改",
    "D": "删除",
    "R": "重命名",
    "C": "复制",
    "U": "冲突",
    "T": "类型变更",
}


def width_of(text):
    """计算包含中英文字符的终端实际显示宽度。"""
    w = 0
    for ch in text:
        east_asian = unicodedata.east_asian_width(ch)
        w += 2 if east_asian in ("W", "F") else 1
    return w


def pad_display(text, target):
    pad = max(0, target - width_of(text))
    return text + (" " * pad)


def status_key(code):
    if code == "??":
        return "??"
    t = code.strip()
    return t[0] if t else "?"


def collect_changes():
    """解析 git status --porcelain=v1。"""
    r = run_cmd(["status", "--porcelain=v1", "--untracked-files=all"], probe=True)
    if r["code"] != 0:
        raise RuntimeError("无法读取 git 状态（当前目录不是 git 仓库？）")

    changes = []
    for line in r["stdout"].splitlines():
        line = line.rstrip()
        if len(line) < 3:
            continue
        code = line[:2]
        file_path = line[3:].strip()
        if file_path.startswith('"') and file_path.endswith('"'):
            file_path = file_path[1:-1]
        if "R" in code and " -> " in file_path:
            file_path = file_path.split(" -> ")[-1].strip()
        changes.append({"code": code, "path": file_path})
    return changes


def summarize(changes):
    buckets = {}
    for item in changes:
        k = status_key(item["code"])
        buckets[k] = buckets.get(k, 0) + 1
    parts = [f"{STATUS_LABEL.get(k, k)} {n}" for k, n in buckets.items()]
    return "，".join(parts)


def print_changes(changes):
    head_count = 40
    labels = [STATUS_LABEL.get(status_key(x["code"]), x["code"]) for x in changes]
    label_width = min(20, max([4] + [width_of(l) for l in labels])) + 2

    for i, item in enumerate(changes[:head_count]):
        mark = pad_display(status_key(item["code"]), 2)
        lbl = pad_display(labels[i], label_width)
        log(f"  {C_YELLOW}{mark}{C_RESET} {lbl}{item['path']}")

    if len(changes) > head_count:
        log(f"  {C_DIM}... 其余 {len(changes) - head_count} 个文件略{C_RESET}")
    info(f"共 {len(changes)} 项变更（{summarize(changes)}）")


def pick_prefix(files):
    if not files:
        return "chore"
    all_docs = all(f.startswith("docs/") or f.lower().endswith((".md", ".mdx", ".txt")) for f in files)
    if all_docs:
        return "docs"
    all_scripts = all(f.startswith("scripts/") for f in files)
    if all_scripts:
        return "chore"
    return "chore"


def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 仓库信息查询


def repo_info():
    inside = run_cmd(["rev-parse", "--is-inside-work-tree"], probe=True)
    if inside["code"] != 0 or inside["stdout"] != "true":
        raise RuntimeError(f"{ROOT} 不是合法的 git 仓库工作区")

    branch_r = run_cmd(["rev-parse", "--abbrev-ref", "HEAD"], probe=True)
    branch = branch_r["stdout"] if branch_r["code"] == 0 else "HEAD"

    remote_r = run_cmd(["remote"], probe=True)
    remotes = [r for r in remote_r["stdout"].splitlines() if r] if remote_r["stdout"] else []

    upstream_r = run_cmd(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], probe=True)
    upstream = upstream_r["stdout"] if upstream_r["code"] == 0 else ""

    ahead = 0
    if upstream:
        ahead_r = run_cmd(["rev-list", "--count", f"{upstream}..HEAD"], probe=True)
        if ahead_r["code"] == 0 and ahead_r["stdout"].isdigit():
            ahead = int(ahead_r["stdout"])

    return {"branch": branch, "remotes": remotes, "upstream": upstream, "ahead": ahead}


# ---------------------------------------------------------------- 参数解析


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        prog="mygit",
        description="一键 git 提交与推送工具（纯 Python 实现，零外部依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-m", "--message", dest="message_flag", help="指定提交信息")
    parser.add_argument("-n", "--dry-run", action="store_true", help="只预览操作，不真正执行")
    parser.add_argument("--no-push", action="store_true", help="提交后不执行推送")
    parser.add_argument("--pull", action="store_true", help="推送前先 git pull --rebase --autostash")
    parser.add_argument("--amend", action="store_true", help="修正上一次提交")
    parser.add_argument("--tag", dest="tag", help="提交后创建附注标签并推送")
    parser.add_argument("rest", nargs="*", help="可选提交信息（未提供 -m 时拼合作为提交说明）")

    args = parser.parse_args()

    message = ""
    if args.message_flag:
        message = args.message_flag.strip()
    elif args.rest:
        message = " ".join(args.rest).strip()

    return {
        "message": message,
        "dryRun": args.dry_run,
        "noPush": args.no_push,
        "pull": args.pull,
        "amend": args.amend,
        "tag": args.tag or "",
    }


# ---------------------------------------------------------------- 推送逻辑


def push_repo(opts, info_before):
    if not info_before["remotes"]:
        warn("未配置任何 git 远端仓库，无法推送。请先配置 git remote add origin <url>")
        return 0

    if opts["pull"]:
        info("推送前先同步远端更新（git pull --rebase --autostash）...")
        pull_r = run_cmd(["pull", "--rebase", "--autostash"])
        if pull_r["code"] != 0:
            fail("git pull --rebase 失败，请处理冲突后重试。")
            return pull_r["code"]

    branch = info_before["branch"]
    if not branch or branch == "HEAD":
        fail("当前处于游离 HEAD 状态，请先切换到具体分支。")
        return 1

    force = ["--force-with-lease"] if opts["amend"] else []
    args = ["push"] + force if info_before["upstream"] else ["push", "-u", "origin", branch] + force

    target_display = info_before["upstream"] or f"origin/{branch}"
    info(f"正在推送至 {target_display} ...")

    if DRY_RUN:
        log(f"{C_DIM}(dry-run) git {' '.join(args)}{C_RESET}")
        ok("dry-run 预览完成：未实际推送")
        return 0

    r = run_captured(args)
    if r["code"] == 0:
        ok(f"推送成功（{target_display}）")
        return 0

    # 遇到非快进被拒，自动拉取 rebase 并重试一次
    output_all = f"{r['stderr']}\n{r['stdout']}"
    rejected = re.search(r"non-fast-forward|rejected|fetch first|Updates were rejected", output_all, re.IGNORECASE)
    if rejected and not opts["amend"]:
        warn("检测到远端有新提交，正在尝试自动 pull --rebase 后重试推送...")
        pull_r = run_cmd(["pull", "--rebase", "--autostash"])
        if pull_r["code"] != 0:
            fail("自动 rebase 失败，请手动解决冲突后再次执行推送。")
            return pull_r["code"]
        r = run_captured(args)
        if r["code"] == 0:
            ok("推送成功（rebase 后重试）")
            return 0

    fail("git push 失败。")
    log(f"{C_DIM}排查建议：检查网络连接、SSH 凭据、分支写入保护权限。{C_RESET}")
    if opts["amend"]:
        log(f"{C_DIM}--amend 改写了历史提交，如确认覆盖可执行: git push --force-with-lease{C_RESET}")
    return r["code"] or 1


# ---------------------------------------------------------------- 主执行流


def main():
    ensure_venv()

    opts = parse_args()

    global DRY_RUN
    DRY_RUN = opts["dryRun"]

    os.chdir(ROOT)

    info_before = repo_info()
    info(f"仓库目录: {ROOT}")
    upstream_str = f" → {info_before['upstream']}" if info_before["upstream"] else "（无 upstream）"
    dry_str = f"  {C_YELLOW}dry-run{C_RESET}" if opts["dryRun"] else ""
    info(f"当前分支: {C_BOLD}{info_before['branch']}{C_RESET}{upstream_str}{dry_str}")

    if not info_before["remotes"]:
        warn("未配置任何 remote，本次改动仅在本机提交")

    changes = collect_changes()

    if not changes:
        if not opts["amend"]:
            info("工作区干净，无未暂存改动。")
            if opts["noPush"]:
                if info_before["ahead"] > 0:
                    info(f"本地有 {info_before['ahead']} 个提交尚未推送（--no-push 已跳过推送）。")
                return 0
            if not info_before["upstream"]:
                info("当前分支尚未建立远端追踪，执行首次推送...")
                return push_repo(opts, info_before)
            if info_before["ahead"] > 0:
                info(f"本地有 {info_before['ahead']} 个已提交内容尚未推送，继续推送...")
                return push_repo(opts, info_before)
            info("本地与远端已完全同步，无需推送。")
            return 0
        warn("--amend 模式下工作区无改动，将仅更新上次提交信息。")
    else:
        log(f"{C_BOLD}待提交变更:{C_RESET}")
        print_changes(changes)

    # 1. git add -A
    add_r = run_cmd(["add", "-A"])
    if add_r["code"] != 0:
        fail("git add 失败，操作已中止。")
        return add_r["code"]

    # 2. 检查暂存区
    staged_r = {"code": 1} if DRY_RUN else run_cmd(["diff", "--cached", "--quiet"], probe=True)
    has_staged = staged_r["code"] != 0
    if not has_staged and not opts["amend"]:
        info("暂存区无新增有效改动（可能被 .gitignore 忽略），跳过提交与推送。")
        return 0

    if has_staged and not DRY_RUN:
        stat_r = run_cmd(["diff", "--cached", "--stat"], probe=True)
        if stat_r["code"] == 0 and stat_r["stdout"]:
            log(f"{C_DIM}{stat_r['stdout']}{C_RESET}")

    # 3. 提交信息生成与 commit
    file_paths = [x["path"] for x in changes]
    default_msg = f"{pick_prefix(file_paths)}: 自动提交 {get_timestamp()}"
    commit_msg = opts["message"] or default_msg
    info(f"提交信息: {C_BOLD}{commit_msg}{C_RESET}")

    commit_args = ["commit", "--amend", "-m", commit_msg] if opts["amend"] else ["commit", "-m", commit_msg]
    commit_r = run_cmd(commit_args)
    if commit_r["code"] != 0:
        fail("git commit 失败，已跳过后续推送。")
        return commit_r["code"]

    if DRY_RUN:
        info("(dry-run) 提交命令未真实写入，以下预览推送步骤。")
    else:
        head_log = run_cmd(["log", "-1", "--format=%h %s"], probe=True)
        ok(f"已提交 {head_log['stdout']}")

    # 4. 可选标签
    if opts["tag"]:
        tag_r = run_cmd(["tag", "-a", opts["tag"], "-m", opts["tag"]])
        if DRY_RUN:
            info(f"(dry-run) 将创建标签 {opts['tag']}")
        elif tag_r["code"] != 0:
            warn(f"创建标签 {opts['tag']} 失败（可能已存在），继续推送提交...")
        else:
            ok(f"已创建附注标签: {opts['tag']}")

    if opts["noPush"]:
        info("--no-push 已指定，跳过推送。")
        return 0

    push_rc = push_repo(opts, repo_info())
    if push_rc != 0:
        return push_rc

    if opts["tag"]:
        tag_push = run_cmd(["push", "origin", opts["tag"]])
        if tag_push["code"] != 0:
            warn(f"标签 {opts['tag']} 推送失败，可稍后手动执行: git push origin {opts['tag']}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        fail("用户中断操作。")
        sys.exit(130)
    except Exception as e:
        fail(f"异常错误: {e}")
        sys.exit(1)
