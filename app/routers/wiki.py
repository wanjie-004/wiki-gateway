"""Search / Read / Files 路由"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from .. import db
from ..auth import get_current_user
from ..models import (
    SearchRequest, SearchResponse, SearchHit,
    FileContent, FilesResponse, FileNode,
    ProjectsResponse, Project,
)
from ..nashsu_client import NashsuClient, get_nashsu_client, NashsuAPIError
from .projects import get_user_project_id


def effective_project_id(user: dict, requested: Optional[str]) -> str:
    """解析实际用的 project_id
    优先级: 显式 requested > 用户 session state > 后端 default
    """
    if requested:
        return requested
    return get_user_project_id(user["id"])

router = APIRouter(prefix="/api", tags=["wiki"])


# ============ Projects ============

@router.get("/projects", response_model=ProjectsResponse)
async def list_projects(
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """列出 nashsu 知识库项目"""
    try:
        data = await nashsu.list_projects()
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    projects = [
        Project(
            id=p["id"],
            name=p["name"],
            path=p["path"],
            current=p.get("current", False),
        )
        for p in data.get("projects", [])
    ]
    current = next((p for p in projects if p.current), None)
    return ProjectsResponse(current_project=current, projects=projects)


# ============ Search ============

@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """搜索 wiki（代理 nashsu 19828 search 端点）"""
    pid = effective_project_id(user, req.project_id)
    try:
        data = await nashsu.search(
            query=req.query,
            top_k=req.top_k,
            include_content=req.include_content,
            project_id=pid,
        )
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    results = [
        SearchHit(
            path=r.get("path", ""),
            title=r.get("title", ""),
            snippet=r.get("snippet", ""),
            score=r.get("score", 0.0),
            title_match=r.get("titleMatch", False),
            vector_score=r.get("vectorScore"),
        )
        for r in data.get("results", [])
    ]

    return SearchResponse(
        query=req.query,
        mode=data.get("mode", "unknown"),
        token_hits=data.get("tokenHits", 0),
        vector_hits=data.get("vectorHits", 0),
        results=results,
    )


# ============ Files (list) ============

@router.get("/files", response_model=FilesResponse)
async def list_files(
    project_id: Optional[str] = Query(default=None, description="项目 ID，不传则用默认"),
    root: str = Query(default="wiki", pattern="^(wiki|sources|all)$"),
    recursive: bool = Query(default=True),
    # nashsu 端 maxFiles 限制: [1, 10000], 但实际 36 文件的项目 maxFiles=10 就触发 413
    # 设大默认防 413, 仍夹到合理上限
    max_files: int = Query(default=2000, ge=1, le=10000),
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """列出项目文件树"""
    pid = effective_project_id(user, project_id)
    try:
        data = await nashsu.list_files(
            project_id=pid,
            root=root,
            recursive=recursive,
            max_files=max_files,
        )
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    return FilesResponse(
        project_id=data.get("projectId", ""),
        root=data.get("root", root),
        files=[FileNode.model_validate(f) for f in data.get("files", [])],
        truncated=data.get("truncated", False),
    )


# ============ Read file ============

@router.get("/read", response_model=FileContent)
async def read_file(
    path: str = Query(..., description="项目内相对路径，如 wiki/concepts/foo.md"),
    project_id: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """读 wiki 文件原文"""
    pid = effective_project_id(user, project_id)
    try:
        data = await nashsu.read_file(pid, path)
    except NashsuAPIError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        if e.status_code == 403:
            raise HTTPException(status_code=403, detail=f"路径不允许: {path}")
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    return FileContent(
        path=data.get("path", path),
        content=data.get("content", ""),
    )


# ============ Health ============

@router.get("/health")
async def health(
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """网关 + nashsu 健康检查"""
    nashsu_health = None
    try:
        nashsu_health = await nashsu.health()
    except Exception as e:
        nashsu_health = {"ok": False, "error": str(e)}

    return {
        "gateway": "ok",
        "nashsu": nashsu_health,
    }
