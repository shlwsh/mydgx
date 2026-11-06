#!/usr/bin/env node
/**
 * 虚拟环境与依赖安装工具（跨平台）
 *
 * 子命令：
 *   venv  创建项目根目录下的独立虚拟环境 .venv
 *   deps  在 .venv 中安装 requirements.txt 依赖（默认使用清华源加速）
 *   all   依次执行 venv + deps
 *
 * 用法：
 *   node scripts/setup_env.mjs venv
 *   node scripts/setup_env.mjs deps
 *   node scripts/setup_env.mjs all
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VENV_DIR = path.join(ROOT, ".venv");
const isWin = process.platform === "win32";
const VENV_PY = path.join(VENV_DIR, isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");
const REQUIREMENTS = path.join(ROOT, "requirements.txt");
const PYPI_MIRROR = process.env.PYPI_INDEX || "https://pypi.tuna.tsinghua.edu.cn/simple";

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: "inherit", ...opts });
  if (r.status !== 0) {
    console.error(`[错误] 命令执行失败: ${cmd} ${args.join(" ")}`);
    process.exit(r.status === null ? 1 : r.status);
  }
}

function findSystemPython() {
  for (const c of isWin ? ["python", "python3"] : ["python3", "python"]) {
    const r = spawnSync(c, ["--version"], { stdio: "ignore" });
    if (r.status === 0) return c;
  }
  console.error("[错误] 未找到系统 Python，请先安装 Python 3.10+");
  process.exit(127);
}

function createVenv() {
  if (fs.existsSync(VENV_PY)) {
    console.log(`[跳过] 虚拟环境已存在: ${VENV_DIR}`);
    return;
  }
  console.log(`[创建] 虚拟环境: ${VENV_DIR}`);
  run(findSystemPython(), ["-m", "venv", VENV_DIR]);
}

function installDeps() {
  if (!fs.existsSync(VENV_PY)) {
    console.log("[提示] 虚拟环境不存在，先执行创建");
    createVenv();
  }
  if (!fs.existsSync(REQUIREMENTS)) {
    console.error(`[错误] 依赖清单不存在: ${REQUIREMENTS}`);
    process.exit(2);
  }
  console.log(`[安装] 依赖来源: ${PYPI_MIRROR}`);
  run(VENV_PY, ["-m", "pip", "install", "--upgrade", "pip", "-i", PYPI_MIRROR]);
  run(VENV_PY, ["-m", "pip", "install", "-r", REQUIREMENTS, "-i", PYPI_MIRROR]);
  console.log("[完成] 依赖安装结束");
}

const cmd = process.argv[2] || "all";
switch (cmd) {
  case "venv":
    createVenv();
    break;
  case "deps":
    installDeps();
    break;
  case "all":
    createVenv();
    installDeps();
    break;
  default:
    console.error(`[错误] 未知子命令: ${cmd}（可用: venv / deps / all）`);
    process.exit(2);
}
