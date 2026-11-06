#!/usr/bin/env node
/**
 * mygit 兼容转发入口（项目核心脚本已全面统一为 Python 版 scripts/mygit.py）
 */

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PY_SCRIPT = path.join(ROOT, "scripts", "mygit.py");

const r = spawnSync("python", [PY_SCRIPT, ...process.argv.slice(2)], {
  cwd: ROOT,
  stdio: "inherit",
});

process.exit(r.status === null ? 1 : r.status);
