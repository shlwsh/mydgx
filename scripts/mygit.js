// 通用 git 提交与推送脚本
// 用法：bun run mygit ["提交信息"]
// 说明：默认提交信息包含日期时间；自动 add -A、commit、push。

import { execSync } from "node:child_process";

function run(cmd) {
  console.log(`> ${cmd}`);
  execSync(cmd, { stdio: "inherit" });
}

function now() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  );
}

const argued = process.argv.slice(2).join(" ");
const defaultMsg = `docs: 自动提交 ${now()}`;

const message = argued || defaultMsg;

try {
  run("git add -A");
  run(`git commit -m "${message}"`);
  run("git push");
  console.log("提交并推送成功.");
} catch (err) {
  if (err && err.status === 1) {
    console.log("无可提交内容或提交被拒，请检查输出。");
  } else {
    console.error("执行失败：", err.message);
  }
  process.exit(err && typeof err.status === "number" ? err.status : 1);
}
