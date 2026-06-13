"""Projects 路由 — 列出/切换 nashsu 项目"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..auth import get_current_user
from ..nashsu_client import NashsuClient, get_nashsu_client, NashsuAPIError
from ..config import settings

router = APIRouter(prefix="/api", tags=["projects"])


class Project(BaseModel):
    id: str
    name: str
    path: str
    current: bool = False


class ProjectsResponse(BaseModel):
    current_project_id: str
    projects: list[Project]


@router.get("/projects", response_model=ProjectsResponse)
async def list_projects(
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """列出所有 nashsu 项目 + 当前 default"""
    try:
        data = await nashsu.list_projects()
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    raw_projects = data.get("projects", [])
    current = data.get("currentProject", {})
    current_id = current.get("id", settings.nashsu_project_id)

    projects = [
        Project(
            id=p.get("id", ""),
            name=p.get("name", ""),
            path=p.get("path", ""),
            current=p.get("current", False) or p.get("id") == current_id,
        )
        for p in raw_projects
    ]
    return ProjectsResponse(
        current_project_id=current_id,
        projects=projects,
    )


class SelectProjectRequest(BaseModel):
    project_id: str


@router.post("/projects/select")
async def select_project(
    body: SelectProjectRequest,
    user: dict = Depends(get_current_user),
):
    """切换当前 default project (session 级, 不改 settings)

    注意: settings.nashsu_project_id 是进程级, 改它需要重启
    session 级切换: 把 project_id 存到 user 会话里 (这里简化: 用一个 module-level state)
    """
    # 简化实现: 用一个 module-level dict 存每个用户的当前 project
    # 生产应该用 Redis / DB 持久化
    from . import projects_state
    projects_state.set(user["id"], body.project_id)
    return {"ok": True, "current_project_id": body.project_id}


def get_user_project_id(user_id: int) -> str:
    """获取用户当前选中的 project_id (per-user 覆盖 default)"""
    from . import projects_state
    return projects_state.get(user_id) or settings.nashsu_project_id
