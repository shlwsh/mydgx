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
const MIRROR_CANDIDATES = [
  process.env.PYPI_INDEX,
  "https://mirrors.aliyun.com/pypi/simple/",
  "https://mirrors.cloud.tencent.com/pypi/simple/",
  "https://pypi.tuna.tsinghua.edu.cn/simple",
].filter(Boolean);

function tryInstallWithMirrors(args) {
  let lastError = null;
  for (const mirror of MIRROR_CANDIDATES) {
    console.log(`[尝试安装] 镜像源: ${mirror}`);
    const r = spawnSync(VENV_PY, ["-m", "pip", ...args, "-i", mirror], {
      cwd: ROOT,
      stdio: "inherit",
    });
    if (r.status === 0) {
      return true;
    }
    console.warn(`[警告] 镜像源 ${mirror} 安装失败，尝试下一镜像源...`);
    lastError = r;
  }
  return false;
}

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
  console.error("[错误] 未找到系统 Python，请先安装 Python 3.8+");
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

  // 1. 尝试升级 pip
  tryInstallWithMirrors(["install", "--upgrade", "pip"]);

  // 2. 安装项目依赖（优先预编译 wheel，避免本地无编译环境时从源码编译失败）
  const success = tryInstallWithMirrors(["install", "--prefer-binary", "-r", REQUIREMENTS]);
  if (!success) {
    console.error("[错误] 所有镜像源均尝试完毕，依赖安装失败");
    process.exit(1);
  }
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
