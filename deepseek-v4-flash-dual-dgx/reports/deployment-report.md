# DeepSeek-V4-Flash-0731 Deployment Report

## Nodes
- Node 0 (Rank 0 / API): 172.19.51.123 (cube-fe5e, DGX Spark GB10)
- Node 1 (Rank 1 / Worker): 172.19.49.159 (cube-0137, DGX Spark GB10)

## Direct Link
- Node 0 CX-7 interface: `enp1s0f0np0` (RoCE HCA rocep1s0f0)
- Node 0 direct IP: `192.168.100.10/24`
- Node 1 CX-7 interface: `enp1s0f0np0` (RoCE HCA rocep1s0f0)
- Node 1 direct IP: `192.168.100.11/24`
- Negotiated link speed: 200 Gb/s per port
- Persistent config: `/etc/netplan/40-cx7-deepseek.yaml` (backup at `/var/backups/deepseek-v4/`)

## RDMA
- Device: rocep1s0f0 (ConnectX-7), GID index 3 (RoCE v2, 192.168.100.x)
- ib_write_bw: ~12,990 MB/s (~13 GB/s, ~104 Gb/s)
- Result: PASS

## NCCL
- NCCL version: 2.30.7-1 (built from source, sm_121)
- all_gather (16 GB, 2 nodes): busbw 22.2 GB/s, 0 errors
- Transport: RoCE (NET/IB via rocep1s0f0 / roceP2p1s0f0); OOB bootstrap on mgmt `wlP9s9`
- Result: PASS

## NIM
- Image: `nvcr.io/nim/deepseek-ai/deepseek-v4-flash-0731:1.11.0-variant`
- Rank 0 container: `deepseek-v4-rank0` (Node 0)
- Rank 1 container: `deepseek-v4-rank1` (Node 1)
- Primary node: 192.168.100.10 (CX-7 direct link)
- Manager port: 20000
- API port: 8000 (Node 0)
- Model cache: `/opt/deepseek-v4/nim-cache` (~190 GB per node)
- Model size: ~177 GB (48 safetensors shards, fp8)

## API
- `/v1/models`: PASS (returns `deepseek-ai/DeepSeek-V4-Flash-0731`)
- `/v1/chat/completions`: PASS (returned "DGX_CLUSTER_OK")

## Distributed Verification
- Node 0 load: GPU util 1% -> 88% during inference
- Node 1 load: GPU util 0% -> 90-95% during same request
- CX-7 RX/TX: Node 1 direct link RX +2.64 MB / TX +1.97 MB during a 1024-token generation
- Result: PASS (both nodes actively compute; inter-node traffic on CX-7)

## Benchmark (baseline, includes cold autotuning)
- 1K prompt / 128 out / c1: 8.9 s end-to-end, 14.4 tps
- 8K prompt / 205 out / c1: 24.4 s, 8.4 tps
- 32K prompt / 136 out / c1: 20.8 s, 6.5 tps
- Concurrency 2 (2K/256): agg 22.4 tps
- Concurrency 4 (2K/256): agg 34.0 tps
- Node memory: ~112 GB / 121 GB used on each node (unified memory)
- Note: first requests trigger SGLang autotuning; throughput improves after warmup. TTFT dominated by distributed prefill.

## Final Result
PASS — dual-node DeepSeek-V4-Flash-0731 serving at http://172.19.51.123:8000/v1

## Remaining Issues / Notes
1. NGC/geo restrictions: all downloads and API calls to `api.ngc.nvidia.com` must egress via non-Chinese proxy. Runtime containers use an SSH tunnel (`127.0.0.1:7899` -> Node 1 FlClash 127.0.0.1:7890 over CX-7) for the manifest fetch; model blobs are fetched directly from `xfiles.ngc.nvidia.com` (no geo-block) via NO_PROXY.
2. Node 0 port 8000 was previously held by the Portainer container (edge-agent port); Portainer was recreated without the 8000 host mapping (9000/9443 kept).
3. Docker daemon on both nodes configured with nvidia runtime (`nvidia-ctk runtime configure`) and a local HTTP proxy drop-in (`/etc/systemd/system/docker.service.d/http-proxy.conf`).
4. Two persistent systemd units exist on Node 0: `proxy-tunnel` (Wi-Fi path, 7898) and `proxy-tunnel2` (CX-7 path, 7899). Only `proxy-tunnel2` is used by the runtime.
5. Node 0's own Clash (7897) was flaky/unusable at deploy time; Node 1's FlClash (7890) is the working egress.
