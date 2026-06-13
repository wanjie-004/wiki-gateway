"""KB Admin 路由 — 知识库 CRUD (阶段 1 复刻 nashsu GUI)

4 端点:
- POST   /api/admin/kb/create         建知识库 (按 username/kb-name 路径)
- GET    /api/admin/kb/list           列所有知识库 (代理 nashsu 19828)
- GET    /api/admin/kb/{id}           单个知识库详情
- DELETE /api/admin/kb/{id}          删知识库 (改 app-state.json 4 处 + 删目录)

路径规则 (按 2026-06-13 C 决策):
- 模式 A (settings.wiki_root_per_user=False, 默认):  wiki_root_user_base/<kb-name>/
- 模式 B (settings.wiki_root_per_user=True):       wiki_root_user_base/<username>/<kb-name>/
"""
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..nashsu_client import (
    NashsuClient, get_nashsu_client, NashsuAPIError,
    create_project_dir as nc_create_project_dir,
    delete_project_state as nc_delete_project_state,
)
from ..config import settings

router = APIRouter(prefix="/api/admin/kb", tags=["kb-admin"])


# ============ 5 模板定义 (拷 nashsu src/lib/templates.ts, 简化版) ============

TEMPLATES = {
    "general": {
        "id": "general",
        "name": "通用",
        "name_en": "General",
        "description": "最小化初始结构, 适合从空白开始",
        "description_en": "Minimal setup — a blank slate for any purpose",
        "icon": "📄",
        "extra_dirs": [],
    },
    "research": {
        "id": "research",
        "name": "研究",
        "name_en": "Research",
        "description": "用于深度研究, 包含假设跟踪和方法论记录",
        "description_en": "Deep-dive research with hypothesis tracking and methodology notes",
        "icon": "🔬",
        "extra_dirs": ["wiki/methodology", "wiki/findings", "wiki/thesis"],
    },
    "reading": {
        "id": "reading",
        "name": "阅读",
        "name_en": "Reading",
        "description": "跟踪书籍的人物、主题、情节线和章节笔记",
        "description_en": "Track a book characters themes plot threads and chapter notes",
        "icon": "📚",
        "extra_dirs": ["wiki/characters", "wiki/themes", "wiki/plot-threads", "wiki/chapters"],
    },
    "personal": {
        "id": "personal",
        "name": "个人成长",
        "name_en": "Personal Growth",
        "description": "记录目标、习惯、反思和成长日志",
        "description_en": "Track goals habits reflections and journal entries for self-improvement",
        "icon": "🌱",
        "extra_dirs": ["wiki/goals", "wiki/habits", "wiki/reflections", "wiki/journal"],
    },
    "business": {
        "id": "business",
        "name": "商务",
        "name_en": "Business",
        "description": "管理团队会议、决策、项目和干系人背景",
        "description_en": "Manage meetings decisions projects and stakeholder context for a team",
        "icon": "💼",
        "extra_dirs": ["wiki/meetings", "wiki/decisions", "wiki/projects", "wiki/stakeholders"],
    },
}


TEMPLATE_SCHEMAS = {
    "general": """# Wiki Schema

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people tools organizations datasets) |
| concept | wiki/concepts/ | Ideas techniques phenomena frameworks |
| source | wiki/sources/ | Papers articles talks books blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |

## Naming Conventions

- Files: kebab-case.md
- Entities: match official name where possible
- Sources: author-year-slug.md

## Frontmatter

```yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```
""",
    "research": """# Wiki Schema — Research Deep-Dive

## Page Types (基础 7 个 + 3 个 research 专属)

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas techniques |
| source | wiki/sources/ | Papers articles |
| query | wiki/queries/ | Open questions |
| comparison | wiki/comparisons/ | Side-by-side analysis |
| synthesis | wiki/synthesis/ | Cross-cutting summaries |
| overview | wiki/ | High-level project summary |
| thesis | wiki/thesis/ | Working hypothesis and its evolution |
| methodology | wiki/methodology/ | Research methods protocols and study designs |
| finding | wiki/findings/ | Individual empirical results or observations |

## Frontmatter

Thesis pages include:
```yaml
confidence: low | medium | high
status: speculative | supported | refuted | settled
```

Finding pages include:
```yaml
source: source-slug
confidence: low | medium | high
replicated: true | false | null
```
""",
    "reading": """# Wiki Schema — Reading a Book

## Page Types (基础 7 个 + 4 个 reading 专属)

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas |
| source | wiki/sources/ | Source summaries |
| query | wiki/queries/ | Open questions |
| comparison | wiki/comparisons/ | Side-by-side analysis |
| synthesis | wiki/synthesis/ | Summaries |
| overview | wiki/ | High-level project summary |
| character | wiki/characters/ | People and figures in the book |
| theme | wiki/themes/ | Recurring ideas motifs and symbolic threads |
| plot-thread | wiki/plot-threads/ | Storylines or narrative arcs |
| chapter | wiki/chapters/ | Per-chapter notes and summaries |

## Frontmatter

Character pages include:
```yaml
first_appearance: Ch. N
role: protagonist | antagonist | supporting | minor
```

Chapter pages include:
```yaml
chapter: N
pages: 1-24
```
""",
    "personal": """# Wiki Schema — Personal Growth

## Page Types (基础 7 个 + 4 个 personal 专属)

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas |
| source | wiki/sources/ | Sources |
| query | wiki/queries/ | Open questions |
| comparison | wiki/comparisons/ | Side-by-side analysis |
| synthesis | wiki/synthesis/ | Summaries |
| overview | wiki/ | High-level project summary |
| goal | wiki/goals/ | Specific outcomes |
| habit | wiki/habits/ | Recurring behaviours and tracking |
| reflection | wiki/reflections/ | Periodic reviews and lessons |
| journal | wiki/journal/ | Freeform daily/session entries |

## Frontmatter

Goal pages include:
```yaml
target_date: YYYY-MM-DD
status: active | paused | achieved | abandoned
progress: 0-100
```

Habit pages include:
```yaml
frequency: daily | weekly | monthly
streak: N
status: active | paused | dropped
```
""",
    "business": """# Wiki Schema — Business / Team

## Page Types (基础 7 个 + 4 个 business 专属)

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | People teams organizations |
| concept | wiki/concepts/ | Ideas methods |
| source | wiki/sources/ | Documents |
| query | wiki/queries/ | Open questions |
| comparison | wiki/comparisons/ | Side-by-side analysis |
| synthesis | wiki/synthesis/ | Summaries |
| overview | wiki/ | High-level project summary |
| meeting | wiki/meetings/ | Meeting notes agendas action items |
| decision | wiki/decisions/ | ADR-style decisions |
| project | wiki/projects/ | Project briefs status retrospectives |
| stakeholder | wiki/stakeholders/ | People teams organizations involved |

## Frontmatter

Meeting pages include:
```yaml
date: YYYY-MM-DD
attendees: []
action_items: []
```

Decision pages include:
```yaml
status: proposed | accepted | deprecated | superseded
deciders: []
date: YYYY-MM-DD
supersedes: ""
```

Project pages include:
```yaml
status: planned | active | on-hold | complete | cancelled
owner: ""
start_date: YYYY-MM-DD
target_date: YYYY-MM-DD
```
""",
}


TEMPLATE_PURPOSES = {
    "general": """# Project Purpose

## Goal
<!-- What are you trying to understand or build? -->

## Key Questions
1.
2.
3.

## Scope
**In scope:**
-

**Out of scope:**
-
""",
    "research": """# Project Purpose — Research Deep-Dive

## Research Question
<!-- Central question this research aims to answer. Be specific and falsifiable. -->

## Hypothesis / Working Thesis
<!-- Your current best guess. This will evolve — update as evidence accumulates. -->

## Background
<!-- What prior work or context motivates this research? -->

## Sub-questions
1.
2.
3.
4.

## Scope
**In scope:**
-

**Out of scope:**
-

## Methodology
<!-- How will you investigate this? -->

## Success Criteria
<!-- How will you know when you have a satisfying answer? -->
""",
    "reading": """# Project Purpose — Reading

## Book Details
**Title:**
**Author:**
**Year:**
**Genre:**

## Why I am Reading This
<!-- What drew you to this book? What do you hope to get from it? -->

## Key Themes to Track
1.
2.
3.

## Questions Going In
1.
2.

## Reading Pace
**Started:**
**Target finish:**
**Current chapter:**
""",
    "personal": """# Project Purpose — Personal Growth

## Focus Areas
<!-- What areas of your life or self are you actively working on? -->
1.
2.
3.

## Motivation
<!-- Why now? What prompted you to start this wiki? -->

## Current Goals (Summary)
- [ ]
- [ ]
- [ ]

## Active Habits
-

## Review Cadence
**Daily journal:** Yes / No
**Weekly reflection:**
**Monthly reflection:**
**Quarterly reflection:**
""",
    "business": """# Project Purpose — Business / Team

## Business Context
**Organisation / Team:**
**Domain:**
**Time period covered:**

## Objectives
1.
2.
3.

## Key Projects
-

## Key Stakeholders
-

## Open Decisions
-

## Metrics / Success Criteria
-

## Constraints and Risks
-

## Review Cadence
**Weekly sync notes:**
**Monthly status update:**
**Quarterly retrospective:**
""",
}


# ============ Pydantic models ============

class TemplateInfo(BaseModel):
    """5 模板之一 (i18n 用 name)"""
    id: str
    name: str
    name_en: str
    description: str
    description_en: str
    icon: str
    extra_dirs: list[str]


class TemplateListResponse(BaseModel):
    templates: list[TemplateInfo]


class KBCreateRequest(BaseModel):
    """POST /api/admin/kb/create 请求体"""
    kb_name: str = Field(min_length=1, max_length=100, description="知识库名")
    template_id: str = Field(description="5 模板 id 之一: general/research/reading/personal/business")
    language: str = Field(default="zh", description="AI 输出语言 (zh/en/...)")
    base_path: Optional[str] = Field(default=None, description="可选: 自定义存储路径, 缺省用 settings.wiki_root_user_base")


class KBCreateResponse(BaseModel):
    ok: bool
    project_id: str
    kb_name: str
    template_id: str
    absolute_path: str
    username: str
    language: str
    schema_written: bool
    purpose_written: bool
    extra_dirs_created: list[str]


class KBSummary(BaseModel):
    """GET /api/admin/kb/list 单元"""
    id: str
    name: str
    path: str
    current: bool = False


class KBListResponse(BaseModel):
    ok: bool
    count: int
    projects: list[KBSummary]


class KBDeleteResponse(BaseModel):
    ok: bool
    deleted_app_state: bool
    deleted_dir: bool
    project_path: Optional[str] = None


# ============ Helper ============

def _derive_project_id(username: str, kb_name: str) -> str:
    """派生 nashsu 风格的 UUID (跟 nashsu 端读到的 id 兼容)"""
    raw = f"{username}:{kb_name}".encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:36]


# ============ 端点 1: GET /api/admin/kb/templates ============

@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    user: dict = Depends(get_current_user),
):
    """列 5 模板 (公开端点, 任何登录用户可看)"""
    items = [
        TemplateInfo(
            id=t["id"],
            name=t["name"],
            name_en=t["name_en"],
            description=t["description"],
            description_en=t["description_en"],
            icon=t["icon"],
            extra_dirs=t["extra_dirs"],
        )
        for t in TEMPLATES.values()
    ]
    return TemplateListResponse(templates=items)


# ============ 端点 2: POST /api/admin/kb/create ============

@router.post("/create", response_model=KBCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_kb(
    body: KBCreateRequest,
    user: dict = Depends(get_current_user),
):
    """创建知识库 (按 username/kb-name 路径)

    路径: <settings.wiki_root_user_base>[/<username>]/<kb_name>/
    nashsu 19828 端实时读文件系统, 自动出现新项目
    """
    username = user["username"]
    kb_name = body.kb_name.strip()
    template_id = body.template_id.strip().lower()

    if template_id not in TEMPLATES:
        raise HTTPException(status_code=400, detail=f"未知模板: {template_id}. 可用: {list(TEMPLATES.keys())}")

    from ..nashsu_client import resolve_kb_path, sanitize_path_segment
    try:
        target_path = resolve_kb_path(username, kb_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"路径非法: {e}")

    if body.base_path:
        target_path = Path(body.base_path) / sanitize_path_segment(kb_name)

    if target_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"目录已存在: {target_path}. 请改名 kb_name 或删旧目录",
        )

    project_id = _derive_project_id(username, kb_name)

    template_data = {
        "schema_md": TEMPLATE_SCHEMAS[template_id],
        "purpose_md": TEMPLATE_PURPOSES[template_id],
        "extra_dirs": TEMPLATES[template_id]["extra_dirs"],
    }
    try:
        result = await nc_create_project_dir(kb_name, template_data, username)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"路径冲突: {target_path}")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"权限不足: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建失败: {e}")

    output_lang_file = target_path / '.output_language'
    output_lang_file.write_text(body.language, encoding='utf-8')

    # 用后端同步注册的 project_id (从 create_project_dir 返), 替代前端派生的 hash
    final_project_id = result.get('project_id') or _derive_project_id(username, kb_name)
    registered = result.get('registered', False)

    return KBCreateResponse(
        ok=True,
        project_id=final_project_id,
        kb_name=kb_name,
        template_id=template_id,
        absolute_path=result['path'],
        username=username,
        language=body.language,
        schema_written=result['schema_written'],
        purpose_written=result['purpose_written'],
        extra_dirs_created=result['extra_dirs_created'],
    )


# ============ 端点 3: GET /api/admin/kb/list ============

@router.get("/list", response_model=KBListResponse)
async def list_kb(
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """列所有知识库 (代理 nashsu 19828 /projects)"""
    try:
        data = await nashsu.list_projects()
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    raw_projects = data.get("projects", [])
    current = data.get("currentProject", {})
    current_id = current.get("id", "")

    items = [
        KBSummary(
            id=p.get("id", ""),
            name=p.get("name", ""),
            path=p.get("path", ""),
            current=(p.get("id", "") == current_id),
        )
        for p in raw_projects
    ]
    return KBListResponse(ok=True, count=len(items), projects=items)


# ============ 端点 4: GET /api/admin/kb/{project_id} ============

@router.get("/{project_id}", response_model=KBSummary)
async def get_kb(
    project_id: str,
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """单个知识库详情 (从 nashsu /projects 列表找)"""
    try:
        data = await nashsu.list_projects()
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    raw_projects = data.get("projects", [])
    current = data.get("currentProject", {})
    current_id = current.get("id", "")

    for p in raw_projects:
        if p.get("id") == project_id:
            return KBSummary(
                id=p.get("id", ""),
                name=p.get("name", ""),
                path=p.get("path", ""),
                current=(p.get("id", "") == current_id),
            )
    raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")


# ============ 端点 5: DELETE /api/admin/kb/{project_id} ============

@router.delete("/{project_id}", response_model=KBDeleteResponse)
async def delete_kb(
    project_id: str,
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """删知识库 (按 05-app-state.md §4 Python 代码: 改 app-state.json 4 处 + 删目录)"""
    is_admin = user.get("role") == "admin"

    try:
        data = await nashsu.list_projects()
    except NashsuAPIError as e:
        raise HTTPException(status_code=502, detail=f"nashsu API 错误: {e.message}")

    project_path = None
    for p in data.get("projects", []):
        if p.get("id") == project_id:
            project_path = p.get("path", "")
            break

    if not project_path:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")

    # 普通用户: 路径必须含 username 根 (防越权)
    if not is_admin:
        from ..nashsu_client import sanitize_path_segment
        username = user["username"]
        safe_user = sanitize_path_segment(username)
        if f"/{safe_user}/" not in project_path and f"/{safe_user}" != project_path.rstrip("/"):
            raise HTTPException(
                status_code=403,
                detail=f"权限不足: 项目不在你的根目录下",
            )

    try:
        result = await nc_delete_project_state(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")

    return KBDeleteResponse(
        ok=True,
        deleted_app_state=result.get("deleted_app_state", False),
        deleted_dir=result.get("deleted_dir", False),
        project_path=result.get("project_path"),
    )
