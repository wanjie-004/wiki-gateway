"""Graph 路由（知识图谱）"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..auth import get_current_user
from ..models import GraphResponse, GraphNode, GraphEdge
from ..nashsu_client import NashsuClient, get_nashsu_client, NashsuAPIError
from .projects import get_user_project_id


def effective_project_id(user: dict, requested: Optional[str]) -> str:
    if requested:
        return requested
    return get_user_project_id(user["id"])

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    project_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="按 id/label 过滤"),
    node_type: Optional[str] = Query(default=None, description="按节点类型过滤"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """获取项目知识图谱"""
    pid = effective_project_id(user, project_id)
    try:
        data = await nashsu.graph(
            project_id=pid,
            q=q,
            node_type=node_type,
            limit=limit,
        )
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    # 收集 nashsu 原始数据, 后续做 label→id 映射
    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])

    nodes = [
        GraphNode(
            id=n["id"],
            label=n["label"],
            node_type=n["nodeType"],
            path=n["path"],
            link_count=n.get("linkCount", 0),
        )
        for n in raw_nodes
    ]

    # nashsu 的 edge.source/target 实际是 node.label (短标题), 不是 node.id
    # 在前端 d3-force 渲染前做映射: label → id
    # 构建 label → id 反向索引 (label 唯一时; 重名取第一个)
    label_to_id: dict[str, str] = {}
    for n in raw_nodes:
        label = n.get("label", "")
        nid = n["id"]
        if label and label not in label_to_id:
            label_to_id[label] = nid

    def resolve(ref: str) -> str:
        """nashsu edge 端点 (label 或 id) → node id"""
        if not ref:
            return ref
        # 先看是否本来就是 id
        if any(n["id"] == ref for n in raw_nodes):
            return ref
        # 否则按 label 找
        return label_to_id.get(ref, ref)

    edges = [
        GraphEdge(
            source=resolve(e.get("source", "")),
            target=resolve(e.get("target", "")),
            weight=e.get("weight", 1.0),
        )
        for e in raw_edges
    ]

    return GraphResponse(
        project_id=data.get("projectId", ""),
        nodes=nodes,
        edges=edges,
    )
