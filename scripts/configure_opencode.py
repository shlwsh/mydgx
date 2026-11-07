#!/usr/bin/env python3
"""为双工作站配置 opencode 并接入 DeepSeek V4.1 Flash。

动作：
    1. 校验目标机已安装 opencode；
    2. 备份并写入 ``~/.config/opencode/opencode.json``（保留已有其他配置）；
    3. 使用项目 .env 中的 DEEPSEEK_API_KEY 配置 deepseek provider；
    4. 通过接口与 ``opencode models`` 双重校验可用性。

示例：
    .venv/bin/python scripts/configure_opencode.py --node all
    .venv/bin/python scripts/configure_opencode.py --node node1 --node1-account his_test
    .venv/bin/python scripts/configure_opencode.py --node node0 --check
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import shlex
import sys
import time

from workstation_common import Node, build_nodes, init_logging

logger = init_logging("configure_opencode")

MODEL_ID = "deepseek-flash"
MODEL_NAME = "DeepSeek V4.1 Flash"
BASE_URL = "https://api.deepseek.com"
CONFIG_REL = ".config/opencode/opencode.json"


def load_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")
    return key


def desired_config(api_key: str) -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"deepseek/{MODEL_ID}",
        "provider": {
            "deepseek": {
                "options": {"apiKey": api_key, "baseURL": BASE_URL},
                "models": {
                    MODEL_ID: {
                        "id": MODEL_ID,
                        "name": MODEL_NAME,
                        "tool_call": True,
                        "reasoning": True,
                        "limit": {"context": 1048576, "output": 393216},
                    }
                },
            }
        },
    }


def merge_config(node: Node, remote_path: str, api_key: str) -> dict:
    """读取已有配置并合并 deepseek 段，避免覆盖用户其他 provider。"""
    existing: dict = {}
    try:
        raw = node.run(f"cat {shlex.quote(remote_path)}")
        existing = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        existing = {}
    desired = desired_config(api_key)
    existing["$schema"] = desired["$schema"]
    existing["model"] = desired["model"]
    providers = existing.setdefault("provider", {})
    providers["deepseek"] = desired["provider"]["deepseek"]
    return existing


def check_node(node: Node, remote_path: str) -> bool:
    present = node.run(f"test -f {shlex.quote(remote_path)} && echo yes || echo no") == "yes"
    if not present:
        logger.warning("[%s] 未发现 opencode 配置文件", node.label)
        return False
    raw = node.run(f"cat {shlex.quote(remote_path)}")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[%s] 配置文件不是合法 JSON：%s", node.label, exc)
        return False
    model = cfg.get("model", "")
    api_key = cfg.get("provider", {}).get("deepseek", {}).get("options", {}).get("apiKey", "")
    registered = node.run("opencode models 2>/dev/null | grep -c 'deepseek/deepseek-flash' || true")
    ok = model == f"deepseek/{MODEL_ID}" and bool(api_key) and registered.isdigit() and int(registered) > 0
    logger.info(
        "[%s] 配置检查：默认模型 %s，密钥 %s，opencode 模型注册 %s 条 → %s",
        node.label,
        model or "（未设置）",
        "已配置" if api_key else "缺失",
        registered,
        "✅ 正常" if ok else "⚠️ 不完整",
    )
    return ok


def verify_api(node: Node, api_key: str) -> None:
    """在目标机直接请求 DeepSeek 接口，验证网络、密钥与模型名。"""
    script = (
        "set -o pipefail; "
        f"curl -sS -m 20 {BASE_URL}/models "
        f"-H 'Authorization: Bearer {api_key}' "
        "| tr ',' '\\n' | grep -E '\"id\": *\"deepseek' || true"
    )
    out = node.run(script, check=False)
    if MODEL_ID not in out:
        raise RuntimeError(f"目标机访问 DeepSeek 接口未返回 {MODEL_ID}：{out or '无响应'}")
    logger.info("[%s] 接口校验通过，返回模型：%s", node.label, " ".join(out.split()))


def configure_node(node: Node, api_key: str) -> None:
    version = node.run("opencode --version 2>&1 | head -1", check=False)
    if not version or "command not found" in version:
        raise RuntimeError("目标机未安装 opencode，请先安装后再配置")
    logger.info("[%s] opencode 版本：%s", node.label, version)

    remote_path = posixpath.join(node.home, CONFIG_REL)
    if node.run(f"test -f {shlex.quote(remote_path)} && echo yes || echo no") == "yes":
        backup = f"{remote_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        node.run(f"cp -p {shlex.quote(remote_path)} {shlex.quote(backup)}")
        logger.info("[%s] 已备份原配置到 %s", node.label, backup)

    config = merge_config(node, remote_path, api_key)
    node.write_text(remote_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    logger.info("[%s] 已写入 %s（权限 600）", node.label, remote_path)

    verify_api(node, api_key)
    registered = node.run("opencode models 2>/dev/null | grep 'deepseek/deepseek-flash' || true", check=False)
    if not registered:
        logger.warning("[%s] opencode models 暂未列出 deepseek/deepseek-flash，可能需要重新登录会话", node.label)
    else:
        logger.info("[%s] opencode 已注册模型：%s", node.label, registered)
    check_node(node, remote_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="为双工作站配置 opencode 并接入 DeepSeek V4.1 Flash",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--node", choices=["all", "node0", "node1"], default="all", help="目标节点")
    parser.add_argument(
        "--node1-account", choices=["ops", "his_test"], default="ops",
        help="Node1 使用的 SSH 账号（ops=dgxdeploy，his_test=his_test）",
    )
    parser.add_argument("--check", action="store_true", help="仅检查配置状态")
    parser.add_argument("--keep-going", action="store_true", help="单节点失败后继续处理其他节点")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    if args.verbose:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)

    api_key = load_api_key()
    failures: list[str] = []
    for node in build_nodes(args.node, args.node1_account):
        try:
            node.connect()
            logger.info("[%s] 已连接 %s@%s", node.label, node.user, node.host)
            try:
                path = posixpath.join(node.home, CONFIG_REL)
                if args.check:
                    if not check_node(node, path):
                        failures.append(node.label)
                else:
                    configure_node(node, api_key)
            finally:
                node.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] 处理失败：%s", node.label, exc)
            failures.append(node.label)
            if not args.keep_going:
                break

    if failures:
        logger.error("未成功节点：%s", "、".join(failures))
        return 1
    logger.info("全部节点配置完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
