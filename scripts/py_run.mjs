#!/usr/bin/env node
/**
 * Python 脚本跨平台启动器
 *
 * 用途：
 *   为 npm / bun 的各类 scripts 入口提供统一的 Python 解释器解析，
 *   避免因各平台解释器路径差异（python / python3 / .venv）导致命令失效。
 *
 * 解释器解析优先级：
 *   1. 项目根目录虚拟环境 .venv（Linux/macOS: .venv/bin/python，Windows: .venv\Scripts\python.exe）
 *   2. 环境变量 PYTHON 指定的解释器
 *   3. 系统 python3 / python
 *
 * 用法：
 *   node scripts/py_run.mjs rdp_connect.py node0 --size 1920x1080
 *   node scripts/py_run.mjs check_status.py
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPTS_DIR = path.join(ROOT, "scripts");

/** 按优先级探测可用的 Python 解释器 */
export function resolvePython() {
  const isWin = process.platform === "win32";
  const candidates = [
    path.join(ROOT, ".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python"),
    process.env.PYTHON,
    isWin ? "python" : "python3",
    "python",
  ].filter(Boolean);

  for (const c of candidates) {
    if (c.includes("/") || c.includes("\\")) {
      if (fs.existsSync(c)) return c;
    } else {
      const r = spawnSync(c, ["--version"], { stdio: "ignore" });
      if (r.status === 0) return c;
    }
  }
  return null;
}

const argv = process.argv.slice(2);
if (argv.length === 0) {
  console.error("[错误] 未指定要执行的 Python 脚本名");
  process.exit(2);
}

const [scriptName, ...rest] = argv;
const pyScript = path.join(SCRIPTS_DIR, scriptName);
if (!fs.existsSync(pyScript)) {
  console.error(`[错误] 脚本不存在: ${pyScript}`);
  process.exit(2);
}

const python = resolvePython();
if (!python) {
  console.error("[错误] 未找到可用的 Python 解释器。");
  console.error("请先执行: python3 -m venv .venv");
  console.error("并安装依赖: .venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple");
  process.exit(127);
}

const r = spawnSync(python, [pyScript, ...rest], {
  cwd: ROOT,
  stdio: "inherit",
});

process.exit(r.status === null ? 1 : r.status);
