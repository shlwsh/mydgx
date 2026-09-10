# LiteLLM 虚拟密钥启用与测试专用 key 记录

> 日期：2026-09-10
> 执行：OpenCode（基于仓库 `.env` 与既有部署文档）
> 目标：为 LiteLLM 网关新增"测试验证专用"密钥，并支持多 key 并存 + 后续调用记录分析

---

## 1. 背景与结论

初版部署为 **静态 master_key 单密钥模式**（`sk-dgx-local-2026`），LiteLLM 的
`master_key` 为单值，**无法同时存在两个静态 key**。经确认，LiteLLM v1.98 要支持
"多个 key 并存 + 按 key 分析调用记录"，必须启用 **虚拟 key（Virtual Keys）**，而虚拟
key 依赖 **PostgreSQL**（SQLite 不支持 key 管理）。

因此本次工作：部署 PostgreSQL 16 + 生成 Prisma Client + 启用虚拟 key，并新生成一个
**测试验证专用 key**。原有主密钥继续服务正式客户端，互不影响。

## 2. 变更清单

| 项 | 内容 |
|---|---|
| 主机 | Node0 / 工作站 `cube-f22b`（172.19.9.104），SSH `winbot@172.19.9.104` |
| 配置文件 | `/home/winbot/litellm_config.yaml`（**注意：非旧文档所写的 /home/dgxdeploy/**） |
| 新增软件 | PostgreSQL 16（apt）、prisma 0.15.0（litellm venv） |
| 新数据库 | `litellm`，用户 `litellm_user`（superuser） |
| 环境注入 | `/etc/systemd/system/litellm.service.d/99-pg.conf`（`DATABASE_URL`） |
| 主密钥 | `sk-dgx-local-2026`（**未变**） |
| 测试 key | `dgx-validation-test` → `sk-jACQ6S5DS23Ttjpn6OXnmg` |

## 3. 测试专用 key 信息

| 字段 | 值 |
|---|---|
| alias | `dgx-validation-test` |
| key | `sk-jACQ6S5DS23Ttjpn6OXnmg` |
| 限定模型 | `deepseek-coding` / `deepseek-local` / `deepseek-office` |
| metadata | `purpose=testing`, `owner=validation` |
| 用途 | 测试验证专用，可随时吊销 |

> ⚠️ 该 key 为明文测试凭证，仅限测试；正式客户端请继续使用 `sk-dgx-local-2026`。

## 4. 实施步骤（可复现）

1. **修复 apt 源**：该机 IPv6 不通 + `ports.ubuntu.com` 明文 HTTP 被干扰，
   将 `/etc/apt/sources.list.d/ubuntu.sources` 的 `http://ports.ubuntu.com` 改为
   `https://ports.ubuntu.com`，并加 `-o Acquire::ForceIPv4=true`。
2. **安装 PostgreSQL**：
   ```bash
   sudo apt-get -o Acquire::ForceIPv4=true install -y postgresql postgresql-contrib
   sudo -u postgres psql -c "CREATE USER litellm_user WITH PASSWORD 'litellm_pg_2026' SUPERUSER;"
   sudo -u postgres createdb -O litellm_user litellm
   ```
3. **安装 prisma 并生成 Client**：
   ```bash
   /home/winbot/litellm-venv/bin/pip install prisma -i https://pypi.tuna.tsinghua.edu.cn/simple
   cd /home/winbot/litellm-venv/lib/python3.12/site-packages/litellm_proxy_extras
   export PATH=/home/winbot/litellm-venv/bin:$PATH
   export DATABASE_URL='postgresql://litellm_user:litellm_pg_2026@127.0.0.1:5432/litellm'
   prisma generate
   prisma migrate deploy
   ```
4. **配置注入**：`litellm.service.d/99-pg.conf` 写入 `DATABASE_URL`；
   `litellm_config.yaml` 的 `general_settings` 设 `store_model_in_db: true`。
5. **重启并生成 key**：
   ```bash
   sudo systemctl restart litellm.service litellm-https.service
   curl -X POST http://172.19.9.104:4000/key/generate \
     -H 'Authorization: Bearer sk-dgx-local-2026' -H 'Content-Type: application/json' \
     -d '{"key_alias":"dgx-validation-test","models":["deepseek-coding","deepseek-local","deepseek-office"],"metadata":{"purpose":"testing"}}'
   ```

## 5. 验证结果（实测通过）

| 检查项 | 结果 |
|---|---|
| 网关存活 | `GET /v1/models` → 200 |
| 主密钥 | 200（不受影响） |
| 测试 key 列模型 | 200 |
| 测试 key 推理 | `TEST OK`（deepseek-coding） |
| key 查询 | `/key/info` 返回 alias/spend/models |

## 6. 踩坑记录

| 现象 | 原因 | 处理 |
|---|---|---|
| `/key/generate` 500 `DB not connected` | v1.98 虚拟 key 需 PostgreSQL | 部署 PG |
| 服务崩溃重启循环 | `prisma generate` 未执行，`from prisma import Prisma` 失败 | 生成 Prisma Client |
| apt 报"明文签署文件不可用/NOSPLIT" | IPv6 不通 + HTTP 被 GFW 干扰 | 源改 HTTPS + 强制 IPv4 |
| `prisma generate` 报 `prisma-client-py: not found` | venv bin 不在 PATH | 导出 PATH 后再生成 |
| `/key/generate` 403 Enterprise | 请求带 `tags` 字段（企业版功能） | 去掉 `tags`，改用 `metadata` |

## 7. 用量日志（已启用）

已按需求将 `general_settings.disable_spend_logs` 由 `true` 改为 **`false`** 并重启，
用量记录写入 PostgreSQL 表 `LiteLLM_SpendLogs`。

实测：用测试 key 发一次请求后，`GET /spend/logs` 返回该请求的
`request_id / api_key(token 哈希) / total_tokens / request_duration_ms / model_group` 等，
可按 key 分析调用记录。测试 key 的 `token_id` 为
`7bd5bf4a540d005ded4656e67df164439bf56a70df4073b613e88124d9f54922`。

> 日志按批次落库（有数秒延迟）。查询示例见 §8。

## 8. 待办 / 建议

- **密钥轮换**：测试 key 验证完成后，可经 `/key/delete` 吊销。
- **文档修订**：既有 LiteLLM 手册中的配置路径 `/home/dgxdeploy/` 已过时，应为
  `/home/winbot/`（Node0 迁移至 winbot 工作站后）。
- **备份回归**：配置文件已多次备份（见 `/home/winbot/litellm_config.yaml.bak-*`），
  如需回退到无 DB 模式，恢复最近一次 `.bak-20260909154130` 并移除 systemd drop-in。
- **PostgreSQL 容量**：日志随调用量增长，后续可按需归档 `LiteLLM_SpendLogs`。

## 9. 运维速查

```bash
# 列出现有 key
curl http://172.19.9.104:4000/key/list -H 'Authorization: Bearer sk-dgx-local-2026'
# 查看某 key 用量
curl 'http://172.19.9.104:4000/key/info?key=<key>' -H 'Authorization: Bearer sk-dgx-local-2026'
# 查看全部调用记录(按 key 分析)
curl http://172.19.9.104:4000/spend/logs -H 'Authorization: Bearer sk-dgx-local-2026'
# 直接查 PostgreSQL 用量表
PGPASSWORD=litellm_pg_2026 psql -h 127.0.0.1 -U litellm_user -d litellm \
  -c "SELECT request_id, model_group, total_tokens, request_duration_ms, \"startTime\" FROM \"LiteLLM_SpendLogs\" ORDER BY \"startTime\" DESC LIMIT 10;"
# 吊销测试 key
curl -X POST http://172.19.9.104:4000/key/delete \
  -H 'Authorization: Bearer sk-dgx-local-2026' -H 'Content-Type: application/json' \
  -d '{"keys":["sk-jACQ6S5DS23Ttjpn6OXnmg"]}'
# 服务状态
sudo systemctl status litellm.service litellm-https.service
# PostgreSQL
sudo systemctl status postgresql
```
