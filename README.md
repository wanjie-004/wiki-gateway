# Wiki Gateway

基于 FastAPI 的轻量级 Web 网关，封装 nashsu/llm_wiki 的 19828 API，
为团队提供 **"查询 + 归档优秀结果"** 能力。

> 配套 nashsu GUI 客户端（必须有 nashsu 在跑）

---

## 能力

- 用户注册/登录（JWT 鉴权）
- 多用户隔离（admin / user 角色）
- 搜索 wiki（代理 nashsu 19828 search 端点）
- 读 wiki 文件
- 列项目文件树
- 知识图谱
- **归档优秀结果**（写文件到 wiki 目录 + 触发 nashsu 重新摄取）

## 不做（明确范围）

- 不做 UI（用 curl / 脚本 / 后续可补前端）
- 不做协同编辑
- 不做实时通知 / WebSocket
- 不做用户管理面板（用 admin 调 `/api/auth/users`）

---

## 快速开始

```bash
# 1. 装依赖
/home/dministrator/.local/bin/uv pip install -r requirements.txt \
  --python /mnt/d/wsl/.hermes/hermes-agent/venv/bin/python

# 2. 改 .env（填 nashsu token + JWT secret）
nano .env

# 3. 启动
bash start.sh

# 4. 验证
curl http://127.0.0.1:8765/api/health
```

启动后访问 http://127.0.0.1:8765/docs 看 Swagger UI。

---

## 端点总览

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/` | GET | 否 | 根路径信息 |
| `/api/health` | GET | 否 | 网关 + nashsu 健康检查 |
| `/api/auth/register` | POST | 否 | 注册新用户（返回 JWT） |
| `/api/auth/login` | POST | 否 | OAuth2 password flow 登录 |
| `/api/auth/login-json` | POST | 否 | JSON 形式登录（curl 友好） |
| `/api/auth/me` | GET | 是 | 当前用户信息 |
| `/api/auth/users` | GET | admin | 列出所有用户 |
| `/api/projects` | GET | 是 | 列 nashsu 知识库项目 |
| `/api/search` | POST | 是 | 搜索 wiki |
| `/api/files` | GET | 是 | 列文件树 |
| `/api/read` | GET | 是 | 读 wiki 文件 |
| `/api/graph` | GET | 是 | 知识图谱 |
| `/api/archive` | POST | 是 | 归档优秀结果到 wiki |

---

## 使用示例（curl）

### 1. 注册 + 拿 token

```bash
curl -X POST http://127.0.0.1:8765/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice123","full_name":"Alice"}'
```

返回示例：
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {"id": 6, "username": "alice", "role": "user"}
}
```

### 2. 搜索

```bash
TOKEN="eyJh...
curl -X POST http://127.0.0.1:8765/api/search \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -d '{"query":"团意险 司机","top_k":5}'
```

### 3. 读文件

```bash
curl "http://127.0.0.1:8765/api/read?path=wiki/concepts/团意险.md" \
  -H "Authorization: Bearer *** 
```

### 4. 知识图谱

```bash
curl "http://127.0.0.1:8765/api/graph?limit=20" \
  -H "Authorization: Bearer *** 
```

### 5. 归档优秀结果

```bash
curl -X POST http://127.0.0.1:8765/api/archive \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -d '{
    "title": "团意险高风险客户",
    "content": "# 团意险高风险客户\n\n核心结论：...\n",
    "target_dir": "synthesis",
    "tags": ["归档", "团意险"],
    "source": "wiki/concepts/团意险.md",
    "note": "从 团意险目标客户 页面摘录",
    "trigger_rescan": true
  }'
```

返回示例：
```json
{
  "path": "wiki/synthesis/20260608_归档_团意险高风险客户.md",
  "absolute_path": "/tmp/wiki-mvp/wiki/synthesis/20260608_归档_团意险高风险客户.md",
  "size_bytes": 616,
  "rescan_triggered": true,
  "rescan_result": {"result": {"changedTasks": [...]}}
}
```

---

## 配置

`.env` 文件：

```bash
# nashsu 19828 API
NASHSU_API_BASE=http://127.0.0.1:19828/api/v1
NASHSU_TOKEN=your-n...n
# nashsu 项目 ID（默认项目）
NASHSU_PROJECT_ID=mvp-test-001
# nashsu wiki 根目录（用于归档写文件）
NASHSU_WIKI_ROOT=/tmp/wiki-mvp

# JWT
JWT_SECRET=change...n
# Gateway
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=8765

# Users
ALLOW_OPEN_REGISTRATION=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=***

# Internal
DB_PATH=./data/users.db
```

---

## 部署

### 开发模式

```bash
bash start.sh
```

### 生产模式（推荐用 supervisor）

```bash
# 装 supervisor
sudo apt install -y supervisor

# 写配置
cat > /etc/supervisor/conf.d/wiki-gateway.conf <<'CONF'
[program:wiki-gateway]
command=/mnt/d/wsl/wiki-gateway/start.sh
directory=/mnt/d/wsl/wiki-gateway
autostart=true
autorestart=true
startretries=10
user=dministrator
stdout_logfile=/var/log/wiki-gateway.log
stderr_logfile=/var/log/wiki-gateway.err
CONF

# 启动
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start wiki-gateway
```

---

## 架构

```
┌────────────────────┐
│  curl / 客户端     │
└─────────┬──────────┘
          │ HTTP + JWT
┌─────────▼──────────┐
│  Wiki Gateway      │  FastAPI 8765
│  - auth            │
│  - search proxy    │  调 19828
│  - read proxy      │
│  - graph proxy     │
│  - archive writer  │  写 wiki 文件 + 触发 rescan
└─────────┬──────────┘
          │ HTTP + nashsu token
┌─────────▼──────────┐
│  nashsu 19828 API  │  Rust / Tauri
│  + 19827 Clip Srv  │
│  + Source Watch    │
│  + Two-Step CoT    │
└─────────┬──────────┘
          │ 文件 IO
┌─────────▼──────────┐
│  wiki/             │
│  raw/sources/      │
│  - LLM 摄取        │
│  - LanceDB 向量    │
└────────────────────┘
```

---

## 安全考量

| 风险 | 当前状态 | 生产建议 |
|---|---|---|
| JWT secret | `.env` 默认值 | 改成强随机字符串 |
| admin 密码 | `admin123` | 改强密码 |
| nashsu token | `.env` 明文 | 改用 secret manager |
| 网关端口 | 127.0.0.1 | 加 Nginx 反代 + HTTPS |
| 用户管理 | 仅 admin 列表 | 加邮件激活 / SSO |
| 归档白名单 | 已做（4 个目录） | 加文件大小限制 |
| 写文件权限 | 网关进程能写 wiki 目录 | 考虑用专门的 nashsu 写端点 |

---

## 已知限制

- `maxFiles` 上限 10（nashsu 19828 限制）—— 文件树分页用 `?root=concepts` 切子目录
- 不支持实时通知（nashsu 摄取后，search 可能要等几秒才能看到新内容）
- 没有协同编辑（设计是 web 端只查询 + 归档）

---

## 故障排查

| 现象 | 排查 |
|---|---|
| 网关 502 "nashsu API 错误" | `curl http://127.0.0.1:19828/api/v1/health` 看 nashsu 活着没 |
| 归档后搜不到 | 等 30-60 秒看 nashsu 摄取；用 `cat wiki/log.md` 看摄取日志 |
| 登录 401 | 用户名密码错 / JWT token 过期（默认 24 小时） |
| 文件 API 422 | `maxFiles` 设太大，改 10 以下 |
| 端口被占 | 改 `.env` 的 `GATEWAY_PORT` |

---

## 开发

```bash
# 看所有路由
PYTHONPATH=. /mnt/d/wsl/.hermes/hermes-agent/venv/bin/python -c "
from app.main import app
for r in app.routes:
    if hasattr(r, 'methods'):
        print(f'{list(r.methods)[0]:>6}  {r.path}')
"

# 跑端到端测试
PYTHONPATH=. /mnt/d/wsl/.hermes/hermes-agent/venv/bin/python test_e2e.py
```

---

## 配套工具

- `/tmp/wiki-mvp/archive.py` —— 命令行归档工具
- Chrome Web Clipper（已下到 Windows 桌面）—— 一键剪藏网页

---

## 版本

v0.1.0 —— 2026-06-08

- 多用户 + JWT 鉴权
- 搜索 / 读 / 文件树 / 图谱 代理
- 归档优秀结果（写文件 + 触发 rescan）
- 10/10 端到端测试通过
