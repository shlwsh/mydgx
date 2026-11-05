#!/usr/bin/env node
/**
 * 一键提交 + 推送脚本（零依赖，Bun / Node 均可运行）
 *
 * 用法:
 *   bun run mygit                          # 自动生成提交信息，add -> commit -> push
 *   bun run mygit -- "fix: 修复网关端口"     # 指定提交信息
 *   bun run mygit -m "docs: 补充手册"        # 同上
 *   bun run mygit --dry-run                 # 只预览将要执行的 git 操作，不落盘
 *   bun run mygit --no-push                 # 只提交，不推送
 *   bun run mygit --pull                    # 提交后、推送前先 pull --rebase
 *   bun run mygit --amend                   # 修正上一次提交（推送用 --force-with-lease）
 *   bun run mygit --tag v1.0.0              # 提交后打附注标签并推送该标签
 *   bun run mygit --help
 *
 * 行为约定:
 *   - 在仓库根目录执行（脚本自身定位到上两级目录），不受当前工作目录影响。
 *   - 默认 git add -A，因此 .gitignore 之外的删除/新增都会纳入提交。
 *   - 无 upstream 时自动 `git push -u origin <branch>`。
 *   - push 被拒（非快进）时自动 `git pull --rebase --autostash` 后重试一次。
 *   - 工作区干净但本地有未推送提交时，仍会执行推送。
 *   - 提交信息通过 argv 数组传递，不做 shell 拼接，中文/引号安全。
 */

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------- 基础环境

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const isTTY = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;
const c = {
  reset: isTTY ? "\u001b[0m" : "",
  dim: isTTY ? "\u001b[2m" : "",
  bold: isTTY ? "\u001b[1m" : "",
  red: isTTY ? "\u001b[31m" : "",
  green: isTTY ? "\u001b[32m" : "",
  yellow: isTTY ? "\u001b[33m" : "",
  cyan: isTTY ? "\u001b[36m" : "",
};

const log = (msg = "") => console.log(msg);
const info = (msg) => log(`${c.cyan}[mygit]${c.reset} ${msg}`);
const ok = (msg) => log(`${c.green}[ok]${c.reset} ${msg}`);
const warn = (msg) => log(`${c.yellow}[warn]${c.reset} ${msg}`);
const fail = (msg) => console.error(`${c.red}[error]${c.reset} ${msg}`);

// ---------------------------------------------------------------- git 封装

/**
 * 执行 git 命令。
 * @param {string[]} args git 参数数组（不含 "git" 本身）
 * @param {{capture?: boolean}} [opt] capture=true 时捕获输出，否则实时透传
 * @returns {{code: number, stdout: string, stderr: string}}
 */
function git(args, opt = {}) {
  const capture = Boolean(opt.capture);
  const r = spawnSync("git", ["-c", "core.quotepath=false", ...args], {
    cwd: ROOT,
    encoding: "utf8",
    // 采集输出时保持 stdin 继承，避免 git 需要凭据输入时卡死
    stdio: capture ? ["inherit", "pipe", "pipe"] : "inherit",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (r.error) {
    throw new Error(`无法执行 git: ${r.error.message}`);
  }
  return {
    code: r.status === null ? 1 : r.status,
    stdout: (r.stdout || "").trimEnd(),
    stderr: (r.stderr || "").trimEnd(),
  };
}

/** 同 git()，但要求成功，失败直接抛错。 */
function gitOrThrow(args, opt) {
  const r = git(args, opt);
  if (r.code !== 0) {
    throw new Error(`git ${args.join(" ")} 失败${r.stderr ? `: ${r.stderr}` : ""}`);
  }
  return r;
}

/** 预览/执行统一入口：dry-run 时只跳过写操作，只读查询照常执行。 */
let DRY_RUN = false;
function run(args, opt = {}) {
  const capture = Boolean(opt.capture) || Boolean(opt.probe);
  const pretty = `git ${args.join(" ")}`;
  if (DRY_RUN && !capture) {
    log(`${c.dim}(dry-run) ${pretty}${c.reset}`);
    return { code: 0, stdout: "", stderr: "" };
  }
  const r = git(args, { capture });
  if (capture && process.env.MYGIT_VERBOSE) {
    log(`${c.dim}> ${pretty}${c.reset}`);
  }
  return r;
}

/** 执行并捕获输出，同时回显到终端（便于判断失败原因）。 */
function runCaptured(args) {
  const r = git(args, { capture: true });
  if (r.stdout) log(r.stdout);
  if (r.stderr) log(r.stderr);
  return r;
}

// ---------------------------------------------------------------- 参数解析

function parseArgs(argv) {
  const opts = {
    message: "",
    dryRun: false,
    noPush: false,
    pull: false,
    amend: false,
    tag: "",
    help: false,
  };
  const words = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "-h" || a === "--help") opts.help = true;
    else if (a === "-n" || a === "--dry-run") opts.dryRun = true;
    else if (a === "--no-push") opts.noPush = true;
    else if (a === "--pull") opts.pull = true;
    else if (a === "--amend") opts.amend = true;
    else if (a === "-m" || a === "--message") {
      const v = argv[i + 1];
      if (v === undefined) throw new Error(`${a} 缺少参数值`);
      words.push(v);
      i += 1;
    } else if (a.startsWith("--message=")) words.push(a.slice("--message=".length));
    else if (a === "--tag") {
      const v = argv[i + 1];
      if (!v) throw new Error("--tag 缺少标签名");
      opts.tag = v;
      i += 1;
    } else if (a.startsWith("--tag=")) opts.tag = a.slice("--tag=".length);
    else if (a === "--") {
      words.push(...argv.slice(i + 1));
      break;
    } else if (a.startsWith("-")) throw new Error(`未知参数: ${a}（--help 查看用法）`);
    else words.push(a);
  }
  opts.message = words.join(" ").trim();
  return opts;
}

function printHelp() {
  log(`
${c.bold}mygit${c.reset} — 一键 git 提交与推送

${c.bold}用法${c.reset}
  bun run mygit [选项] [提交信息...]

${c.bold}选项${c.reset}
  -m, --message <msg>   指定提交信息（也可直接跟在命令后面）
  -n, --dry-run         只预览，不执行任何写操作
      --no-push         提交后不推送
      --pull            提交后、推送前先 git pull --rebase --autostash
      --amend           修正上一次提交（推送自动使用 --force-with-lease）
      --tag <name>      提交后创建附注标签并推送
  -h, --help            显示本帮助

${c.bold}示例${c.reset}
  bun run mygit
  bun run mygit -- "docs: 补充 LiteLLM 网关手册"
  bun run mygit -m "fix: 修复端口冲突" --dry-run
`);
}

// ---------------------------------------------------------------- 状态解析

const STATUS_LABEL = {
  "??": "新增(未跟踪)",
  A: "新增",
  M: "修改",
  D: "删除",
  R: "重命名",
  C: "复制",
  U: "冲突",
  T: "类型变更",
};

/** 终端显示宽度（CJK 字符按 2 列计）。 */
function widthOf(text) {
  let w = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    const wide =
      (cp >= 0x1100 && cp <= 0x115f) ||
      (cp >= 0x2e80 && cp <= 0xa4cf) ||
      (cp >= 0xac00 && cp <= 0xd7a3) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0xfe30 && cp <= 0xfe6f) ||
      (cp >= 0xff00 && cp <= 0xff60) ||
      (cp >= 0xffe0 && cp <= 0xffe6);
    w += wide ? 2 : 1;
  }
  return w;
}

function padDisplay(text, target) {
  const pad = Math.max(0, target - widthOf(text));
  return text + " ".repeat(pad);
}

function statusKey(code) {
  if (code === "??") return "??";
  const t = code.trim();
  return t.charAt(0) || "?";
}

/** 解析 git status --porcelain，返回 [{code, path}]。 */
function collectChanges() {
  const r = run(["status", "--porcelain=v1", "--untracked-files=all"], { probe: true });
  if (r.code !== 0) throw new Error("无法读取 git 状态（当前目录不是 git 仓库？）");
  return r.stdout
    .split("\n")
    .map((line) => line.replace(/\s+$/, ""))
    .filter((line) => line.length >= 3)
    .map((line) => {
      const m = line.match(/^(.{2})\s+(.*)$/);
      const code = m ? m[1] : line.slice(0, 2);
      let file = m ? m[2].trim() : line.slice(3).trim();
      if (file.startsWith('"') && file.endsWith('"')) {
        file = file.slice(1, -1);
      }
      // 重命名形如 "R  old -> new"，只展示新路径
      if (code.includes("R") && file.includes(" -> ")) file = file.split(" -> ").pop().trim();
      return { code, path: file };
    });
}

function summarize(changes) {
  const buckets = new Map();
  for (const { code } of changes) {
    const key = statusKey(code);
    buckets.set(key, (buckets.get(key) || 0) + 1);
  }
  return [...buckets.entries()]
    .map(([k, n]) => `${STATUS_LABEL[k] || k} ${n}`)
    .join("，");
}

function printChanges(changes) {
  const HEAD = 40;
  const labels = changes.map(({ code }) => STATUS_LABEL[statusKey(code)] || code);
  const labelWidth = Math.min(20, Math.max(4, ...labels.map(widthOf))) + 2;
  changes.slice(0, HEAD).forEach(({ code, path: file }, i) => {
    const mark = padDisplay(statusKey(code), 2);
    log(`  ${c.yellow}${mark}${c.reset} ${padDisplay(labels[i], labelWidth)}${file}`);
  });
  if (changes.length > HEAD) log(`  ${c.dim}... 其余 ${changes.length - HEAD} 个文件略${c.reset}`);
  info(`共 ${changes.length} 项变更（${summarize(changes)}）`);
}

/** 文件列表全在 docs/ 或都是 markdown -> docs，否则 chore。 */
function pickPrefix(files) {
  if (files.length === 0) return "chore";
  const allDocs = files.every((f) => f.startsWith("docs/") || /\.(md|mdx|txt)$/i.test(f));
  if (allDocs) return "docs";
  const allScripts = files.every((f) => f.startsWith("scripts/"));
  if (allScripts) return "chore";
  return "chore";
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ---------------------------------------------------------------- 仓库信息

function repoInfo() {
  const inside = run(["rev-parse", "--is-inside-work-tree"], { probe: true });
  if (inside.code !== 0 || inside.stdout !== "true") {
    throw new Error(`${ROOT} 不是 git 仓库`);
  }
  const branchR = run(["rev-parse", "--abbrev-ref", "HEAD"], { probe: true });
  const branch = branchR.code === 0 ? branchR.stdout : "HEAD";
  const remoteR = run(["remote"], { probe: true });
  const remotes = remoteR.stdout ? remoteR.stdout.split("\n").filter(Boolean) : [];
  const upstreamR = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], { probe: true });
  const upstream = upstreamR.code === 0 ? upstreamR.stdout : "";
  const aheadR = upstream
    ? run(["rev-list", "--count", `${upstream}..HEAD`], { probe: true })
    : { code: 1, stdout: "" };
  const ahead = aheadR.code === 0 ? Number(aheadR.stdout || 0) : 0;
  return { branch, remotes, upstream, ahead };
}

// ---------------------------------------------------------------- 主流程

function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    fail(err.message);
    return 2;
  }
  if (opts.help) {
    printHelp();
    return 0;
  }
  DRY_RUN = opts.dryRun;

  process.chdir(ROOT);

  const infoBefore = repoInfo();
  info(`仓库 ${ROOT}`);
  info(`分支 ${c.bold}${infoBefore.branch}${c.reset}${infoBefore.upstream ? ` → ${infoBefore.upstream}` : "（无 upstream）"}${opts.dryRun ? `  ${c.yellow}dry-run${c.reset}` : ""}`);

  if (infoBefore.remotes.length === 0) {
    warn("未配置任何 remote，本次只能本地提交");
  }

  const changes = collectChanges();

  if (changes.length === 0) {
    if (!opts.amend) {
      info("工作区干净，没有需要提交的改动。");
      if (opts.noPush) {
        if (infoBefore.ahead > 0) {
          info(`本地有 ${infoBefore.ahead} 个提交尚未推送（--no-push 已跳过推送）。`);
        }
        return 0;
      }
      if (!infoBefore.upstream) {
        info("当前分支尚未跟踪远端，继续执行首次推送（自动建立 upstream）...");
        return push(opts, infoBefore);
      }
      if (infoBefore.ahead > 0) {
        info(`本地有 ${infoBefore.ahead} 个提交尚未推送，继续推送...`);
        return push(opts, infoBefore);
      }
      info("本地与远端已同步，无需推送。");
      return 0;
    }
    warn("--amend 模式下没有新改动，将仅修改上一次提交的信息。");
  } else {
    log(`${c.bold}待提交变更:${c.reset}`);
    printChanges(changes);
  }

  // 1) add -A
  const addR = run(["add", "-A"]);
  if (addR.code !== 0) {
    fail("git add 失败，已中止。");
    return addR.code;
  }

  // 2) 是否真的有内容进暂存区
  const stagedR = DRY_RUN ? { code: 1 } : run(["diff", "--cached", "--quiet"], { probe: true });
  const hasStaged = stagedR.code !== 0;
  if (!hasStaged && !opts.amend) {
    info("暂存区为空（改动可能已被忽略），跳过提交与推送。");
    return 0;
  }
  if (hasStaged && !DRY_RUN) {
    const stat = run(["diff", "--cached", "--stat"], { probe: true });
    if (stat.code === 0 && stat.stdout) {
      log(`${c.dim}${stat.stdout}${c.reset}`);
    }
  }

  // 3) 生成提交信息
  const message = opts.message || `${pickPrefix(changes.map((x) => x.path))}: 自动提交 ${stamp()}`;
  info(`提交信息：${c.bold}${message}${c.reset}`);

  const commitArgs = opts.amend ? ["commit", "--amend", "-m", message] : ["commit", "-m", message];
  const commitR = run(commitArgs);
  if (commitR.code !== 0) {
    fail("git commit 失败，已跳过推送。");
    return commitR.code;
  }

  if (DRY_RUN) {
    info("(dry-run) 提交不会真正执行，以下仅预览推送命令。");
  } else {
    const head = run(["log", "-1", "--format=%h %s"], { probe: true });
    ok(`已提交 ${head.stdout}`);
  }

  // 4) 可选标签
  if (opts.tag) {
    const tagR = run(["tag", "-a", opts.tag, "-m", opts.tag]);
    if (DRY_RUN) {
      info(`(dry-run) 将创建标签 ${opts.tag}`);
    } else if (tagR.code !== 0) {
      warn(`创建标签 ${opts.tag} 失败（已存在？），继续推送提交。`);
    } else {
      ok(`已创建标签 ${opts.tag}`);
    }
  }

  if (opts.noPush) {
    info("--no-push 已指定，跳过推送。");
    return 0;
  }

  const pushRc = push(opts, repoInfo());
  if (pushRc !== 0) return pushRc;

  if (opts.tag) {
    const tagPush = run(["push", "origin", opts.tag]);
    if (tagPush.code !== 0) {
      warn(`标签 ${opts.tag} 推送失败，可稍后手动执行: git push origin ${opts.tag}`);
    }
  }
  return 0;
}

/** 推送（含 upstream 建立、非快进自动 rebase 重试）。 */
function push(opts, infoBefore) {
  if (infoBefore.remotes.length === 0) {
    warn("没有可用 remote，无法推送。可先执行: git remote add origin <url>");
    return 0;
  }

  if (opts.pull) {
    info("推送前先同步远端（pull --rebase --autostash）...");
    const pullR = run(["pull", "--rebase", "--autostash"]);
    if (pullR.code !== 0) {
      fail("git pull --rebase 失败，请解决冲突后重试。");
      return pullR.code;
    }
  }

  const branch = infoBefore.branch;
  if (!branch || branch === "HEAD") {
    fail("当前处于游离 HEAD 状态，请先切换到分支再推送。");
    return 1;
  }

  const force = opts.amend ? ["--force-with-lease"] : [];
  const args = infoBefore.upstream
    ? ["push", ...force]
    : ["push", "-u", "origin", branch, ...force];

  info(`推送至 ${infoBefore.upstream || `origin/${branch}`} ...`);
  if (DRY_RUN) {
    log(`${c.dim}(dry-run) git ${args.join(" ")}${c.reset}`);
    ok("dry-run 预览：推送命令未执行");
    return 0;
  }

  let r = runCaptured(args);
  if (r.code === 0) {
    ok(`推送成功（${infoBefore.upstream || `origin/${branch}`}）`);
    return 0;
  }

  // 非快进 -> 自动 rebase 重试一次
  const rejected = /non-fast-forward|rejected|fetch first|Updates were rejected/i.test(
    `${r.stderr}\n${r.stdout}`,
  );
  if (rejected && !opts.amend) {
    warn("远端有新提交，先 pull --rebase 再重试推送...");
    const pullR = run(["pull", "--rebase", "--autostash"]);
    if (pullR.code !== 0) {
      fail("自动 rebase 失败，请手动解决冲突后执行 git push。");
      return pullR.code;
    }
    r = runCaptured(args);
    if (r.code === 0) {
      ok("推送成功（rebase 后重试）");
      return 0;
    }
  }

  fail("git push 失败。");
  log(`${c.dim}常见原因：凭据失效、远端权限不足、分支保护或网络不可达。${c.reset}`);
  if (opts.amend) {
    log(`${c.dim}--amend 改写了历史，如确认无误可执行: git push --force-with-lease${c.reset}`);
  }
  return r.code || 1;
}

try {
  process.exitCode = main();
} catch (err) {
  fail(err.message || String(err));
  process.exitCode = 1;
}
