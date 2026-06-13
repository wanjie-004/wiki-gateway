"""Pydantic 模型（请求/响应）"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ============ Auth ============

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: "UserInfo"


class UserInfo(BaseModel):
    id: int
    username: str
    full_name: Optional[str]
    role: str  # admin | user
    created_at: datetime


# ============ Search ============

class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)
    include_content: bool = False
    project_id: Optional[str] = None  # 不传则用默认项目


class SearchHit(BaseModel):
    path: str
    title: str
    snippet: str
    score: float
    title_match: bool = False
    vector_score: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    token_hits: int
    vector_hits: int
    results: list[SearchHit]


# ============ Read ============

class FileContent(BaseModel):
    path: str
    content: str


class FileNode(BaseModel):
    model_config = {"populate_by_name": True}  # 支持 isDir 和 is_dir 两种

    name: str
    path: str
    is_dir: bool = Field(alias="isDir")
    size: Optional[int] = None
    children: Optional[list["FileNode"]] = None


class FilesResponse(BaseModel):
    project_id: str
    root: str
    files: list[FileNode]
    truncated: bool = False


# ============ Graph ============

class GraphNode(BaseModel):
    id: str
    label: str
    node_type: str
    path: str
    link_count: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float = 1.0


class GraphResponse(BaseModel):
    project_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ============ Archive ============

class ArchiveRequest(BaseModel):
    """归档优秀结果到 wiki"""
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)  # 要归档的 markdown 内容
    target_dir: str = "synthesis"  # 默认写到 synthesis/，可选 concepts/, queries/
    tags: list[str] = []
    source: Optional[str] = None  # 来源页面（可选）
    note: Optional[str] = None  # 归档备注（可选）
    trigger_rescan: bool = True  # 是否触发 nashsu 重新摄取


class ArchiveResponse(BaseModel):
    path: str  # 写入的文件路径（相对 wiki 根）
    absolute_path: str  # 绝对路径
    size_bytes: int
    rescan_triggered: bool
    rescan_result: Optional[dict] = None


# ============ Projects ============

class Project(BaseModel):
    id: str
    name: str
    path: str
    current: bool


class ProjectsResponse(BaseModel):
    current_project: Optional[Project]
    projects: list[Project]


# Resolve forward references
TokenResponse.model_rebuild()
FileNode.model_rebuild()
