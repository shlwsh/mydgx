#!/bin/bash
echo "=== HOST ==="
hostname
whoami
uname -a
cat /etc/os-release
echo "=== GPU ==="
nvidia-smi || true
echo "=== MEMORY ==="
free -h
echo "=== STORAGE ==="
df -hT
echo "=== NETWORK ==="
ip -br addr
ip route
echo "=== CONNECTX ==="
command -v ibdev2netdev >/dev/null && ibdev2netdev || true
command -v ibv_devices >/dev/null && ibv_devices || true
echo "=== RDMA ==="
ls -la /dev/infiniband 2>/dev/null || true
command -v rdma >/dev/null && rdma link show 2>/dev/null || true
echo "=== DOCKER ==="
docker --version 2>/dev/null || true
docker info 2>/dev/null | head -30 || true
echo "=== NVIDIA CONTAINER ==="
command -v nvidia-ctk || true
echo "=== NETPLAN ==="
ls /etc/netplan/ 2>/dev/null || true
echo "=== PREFLIGHT_DONE ==="
