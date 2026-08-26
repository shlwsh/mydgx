#!/usr/bin/env bash
set -Eeuo pipefail
SSH_KEY="${SSH_KEY:-$HOME/.ssh/dgx_deepseek_v4_ed25519}"

echo "Stopping DeepSeek-V4-Flash cluster..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.49.159 \
  'docker rm -f deepseek-v4-rank1 2>/dev/null || true'
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.51.123 \
  'docker rm -f deepseek-v4-rank0 2>/dev/null || true'
echo "DeepSeek V4 cluster stopped."
