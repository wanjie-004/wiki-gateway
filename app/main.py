"""FastAPI 主入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from . import db
from .routers import auth, wiki, graph, archive, chat, nashsu_config, projects
from .nashsu_client import NashsuClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库"""
    db.init_db()
    chat.init_chat_db()
    # 启动时从 nashsu app-state.json 读 token, 让 RAG 能调 nashsu API
    # 必须先于 get_nashsu_client() 单例首次访问
    tok = NashsuClient.refresh_token()
    if tok:
        _ = tok
    # 启动时加载 per-user project state (从 SQLite 持久化表)
    # 让 nashsu_client.get_user_project_id() 立即可用
    from .routers import projects_state
    loaded = projects_state.load_all()
    print(f"✅ Loaded {loaded} user project state entries from DB")
    if tok:
        print(f"✅ Nashsu token 加载: OK ({len(tok)} chars)")
    else:
        print("⚠️  Nashsu token 为空, RAG 将失败 (nashsu 19828 会 401)")
    print(f"   nashsu_api_base: {settings.nashsu_api_base}")
    print(f"   nashsu_project_id: {settings.nashsu_project_id}")

    # 预热 NashsuClient 单例, 确保它读到 runtime token
    from .nashsu_client import get_nashsu_client
    nc = get_nashsu_client()
    print(f"   NashsuClient.token: {(nc.token or '')[:15]}...")
    print(f"✅ Wiki Gateway 启动")
    print(f"   监听: http://{settings.gateway_host}:{settings.gateway_port}")
    print(f"   nashsu: {settings.nashsu_api_base}")
    print(f"   默认项目: {settings.nashsu_project_id}")
    print(f"   admin 账号: {settings.admin_username} / {settings.admin_password}")
    yield
    print("👋 Wiki Gateway 关闭")


app = FastAPI(
    title="Wiki Gateway",
    description="基于 FastAPI 的 nashsu/llm_wiki Web 网关（查询 + 归档）",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许本地 Next.js dev server (:3000) 和生产静态托管 origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8765",  # 同一 origin（FastAPI 自身服务前端时）
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",  # 任意 localhost 端口
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# 注册路由
app.include_router(auth.router)
app.include_router(wiki.router)
app.include_router(graph.router)
app.include_router(archive.router)
app.include_router(chat.router)
app.include_router(nashsu_config.router)
app.include_router(projects.router)


@app.get("/")
async def root():
    return {
        "name": "Wiki Gateway",
        "version": "0.1.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    """全局异常处理（避免泄露内部错误）"""
    print(f"❌ 未处理异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"网关内部错误: {type(exc).__name__}"},
    )
