#!/usr/bin/env bash
set -Eeuo pipefail
SSH_KEY="${SSH_KEY:-$HOME/.ssh/dgx_deepseek_v4_ed25519}"

echo "Rolling back network config created by this deployment..."
# Remove only the netplan file this deployment added
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.51.123 \
  'sudo rm -f /etc/netplan/40-cx7-deepseek.yaml; sudo netplan generate; sudo netplan apply'
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.49.159 \
  'sudo rm -f /etc/netplan/40-cx7-deepseek.yaml; sudo netplan generate; sudo netplan apply'

echo "Stopping containers (images and model cache are kept)..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.49.159 \
  'docker rm -f deepseek-v4-rank1 2>/dev/null || true'
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.51.123 \
  'docker rm -f deepseek-v4-rank0 2>/dev/null || true'

echo "Removing temporary SSH tunnels on Node 0..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no dgxdeploy@172.19.51.123 \
  'sudo systemctl stop proxy-tunnel2 2>/dev/null || true; sudo systemctl stop proxy-tunnel 2>/dev/null || true'

echo "Rollback done. Original netplan backups kept in /var/backups/deepseek-v4/."
