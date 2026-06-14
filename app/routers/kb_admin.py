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
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |

## Naming Conventions

- Files: \`kebab-case.md\`
- Entities: match official name where possible (e.g., \`openai.md\`, \`gpt-4.md\`)
- Concepts: descriptive noun phrases (e.g., \`chain-of-thought.md\`)
- Sources: \`author-year-slug.md\` (e.g., \`wei-2022-cot.md\`)
- Queries: question as slug (e.g., \`does-scale-improve-reasoning.md\`)

## Frontmatter

All pages must include YAML frontmatter:

\`\`\`yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
\`\`\`

Source pages also include:
\`\`\`yaml
authors: []
year: YYYY
url: ""
venue: ""
\`\`\`

## Index Format

\`wiki/index.md\` lists all pages grouped by type. Each entry:
\`\`\`
- [[page-slug]] — one-line description
\`\`\`

## Log Format

\`wiki/log.md\` records activity in reverse chronological order:
\`\`\`
## YYYY-MM-DD

- Action taken / finding noted
\`\`\`

## Cross-referencing Rules

- Use \`[[page-slug]]\` syntax to link between wiki pages
- Every entity and concept should appear in \`wiki/index.md\`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via \`related:\`

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists
""",

    "research": """# Wiki Schema — Research Deep-Dive

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |
| thesis | wiki/thesis/ | Working hypothesis and its evolution over time |
| methodology | wiki/methodology/ | Research methods, protocols, and study designs |
| finding | wiki/findings/ | Individual empirical results or observations |

## Naming Conventions

- Files: \`kebab-case.md\`
- Entities: match official name where possible (e.g., \`openai.md\`, \`gpt-4.md\`)
- Concepts: descriptive noun phrases (e.g., \`chain-of-thought.md\`)
- Sources: \`author-year-slug.md\` (e.g., \`wei-2022-cot.md\`)
- Queries: question as slug (e.g., \`does-scale-improve-reasoning.md\`)
- Theses: hypothesis as slug (e.g., \`scaling-improves-reasoning.md\`)
- Methodologies: method name (e.g., \`systematic-review.md\`, \`ablation-study.md\`)
- Findings: descriptive slug (e.g., \`larger-models-better-few-shot.md\`)

## Frontmatter

All pages must include YAML frontmatter:

\`\`\`yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
\`\`\`

Source pages also include:
\`\`\`yaml
authors: []
year: YYYY
url: ""
venue: ""
\`\`\`

Thesis pages also include:
\`\`\`yaml
confidence: low | medium | high
status: speculative | supported | refuted | settled
\`\`\`

Finding pages also include:
\`\`\`yaml
source: "[[source-slug]]"
confidence: low | medium | high
replicated: true | false | null
\`\`\`

## Index Format

\`wiki/index.md\` lists all pages grouped by type. Each entry:
\`\`\`
- [[page-slug]] — one-line description
\`\`\`

## Log Format

\`wiki/log.md\` records activity in reverse chronological order:
\`\`\`
## YYYY-MM-DD

- Action taken / finding noted
\`\`\`

## Cross-referencing Rules

- Use \`[[page-slug]]\` syntax to link between wiki pages
- Every entity and concept should appear in \`wiki/index.md\`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via \`related:\`
- Findings link back to their source via the \`source:\` frontmatter field
- Thesis pages reference supporting and refuting findings via \`related:\`
- Methodology pages are cited by the findings that used them

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists

## Research-Specific Conventions

- Keep the thesis pages updated as evidence accumulates — they are living documents
- Every finding should assess replication status when known
- Methodology pages explain the *why* (rationale) not just the *how*
- Distinguish between direct evidence and inference in finding pages
""",

    "reading": """# Wiki Schema — Reading a Book

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |
| character | wiki/characters/ | People and figures in the book |
| theme | wiki/themes/ | Recurring ideas, motifs, and symbolic threads |
| plot-thread | wiki/plot-threads/ | Storylines or narrative arcs being tracked |
| chapter | wiki/chapters/ | Per-chapter notes and summaries |

## Naming Conventions

- Files: \`kebab-case.md\`
- Entities: match official name where possible (e.g., \`openai.md\`, \`gpt-4.md\`)
- Concepts: descriptive noun phrases (e.g., \`chain-of-thought.md\`)
- Sources: \`author-year-slug.md\` (e.g., \`wei-2022-cot.md\`)
- Queries: question as slug (e.g., \`does-scale-improve-reasoning.md\`)
- Characters: character name in kebab-case (e.g., \`elizabeth-bennet.md\`)
- Themes: thematic noun phrase (e.g., \`social-class-mobility.md\`, \`deception-vs-honesty.md\`)
- Plot threads: arc description (e.g., \`darcys-redemption-arc.md\`)
- Chapters: \`ch-NN-slug.md\` (e.g., \`ch-01-opening-scene.md\`)

## Frontmatter

All pages must include YAML frontmatter:

\`\`\`yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
\`\`\`

Source pages also include:
\`\`\`yaml
authors: []
year: YYYY
url: ""
venue: ""
\`\`\`

Character pages also include:
\`\`\`yaml
first_appearance: "Ch. N"
role: protagonist | antagonist | supporting | minor
\`\`\`

Chapter pages also include:
\`\`\`yaml
chapter: N
pages: "1-24"
\`\`\`

## Index Format

\`wiki/index.md\` lists all pages grouped by type. Each entry:
\`\`\`
- [[page-slug]] — one-line description
\`\`\`

## Log Format

\`wiki/log.md\` records activity in reverse chronological order:
\`\`\`
## YYYY-MM-DD

- Action taken / finding noted
\`\`\`

## Cross-referencing Rules

- Use \`[[page-slug]]\` syntax to link between wiki pages
- Every entity and concept should appear in \`wiki/index.md\`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via \`related:\`
- Chapter notes reference characters appearing in that chapter via \`related:\`
- Theme pages link to the chapters where the theme is most prominent
- Plot thread pages list chapters that advance the arc

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists

## Reading-Specific Conventions

- Chapter pages are written during or immediately after reading — capture fresh reactions
- Distinguish between plot summary and personal interpretation in chapter notes
- Theme pages should track *development* across the book, not just state that a theme exists
- Flag unresolved plot threads with status: \`open\` until resolved
- Note page numbers for important quotes to enable re-finding later
""",

    "personal": """# Wiki Schema — Personal Growth

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |
| goal | wiki/goals/ | Specific outcomes you are working toward |
| habit | wiki/habits/ | Recurring behaviours and their tracking |
| reflection | wiki/reflections/ | Periodic reviews and lessons learned |
| journal | wiki/journal/ | Freeform daily or session entries |

## Naming Conventions

- Files: \`kebab-case.md\`
- Entities: match official name where possible (e.g., \`openai.md\`, \`gpt-4.md\`)
- Concepts: descriptive noun phrases (e.g., \`chain-of-thought.md\`)
- Sources: \`author-year-slug.md\` (e.g., \`wei-2022-cot.md\`)
- Queries: question as slug (e.g., \`does-scale-improve-reasoning.md\`)
- Goals: outcome as slug (e.g., \`run-a-marathon.md\`, \`learn-spanish.md\`)
- Habits: behaviour name (e.g., \`daily-meditation.md\`, \`morning-pages.md\`)
- Reflections: type + date (e.g., \`weekly-2024-03.md\`, \`quarterly-2024-q1.md\`)
- Journal: date slug (e.g., \`2024-03-15.md\`)

## Frontmatter

All pages must include YAML frontmatter:

\`\`\`yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
\`\`\`

Source pages also include:
\`\`\`yaml
authors: []
year: YYYY
url: ""
venue: ""
\`\`\`

Goal pages also include:
\`\`\`yaml
target_date: YYYY-MM-DD
status: active | paused | achieved | abandoned
progress: 0-100
\`\`\`

Habit pages also include:
\`\`\`yaml
frequency: daily | weekly | monthly
streak: N
status: active | paused | dropped
\`\`\`

Reflection pages also include:
\`\`\`yaml
period: weekly | monthly | quarterly | annual
\`\`\`

## Index Format

\`wiki/index.md\` lists all pages grouped by type. Each entry:
\`\`\`
- [[page-slug]] — one-line description
\`\`\`

## Log Format

\`wiki/log.md\` records activity in reverse chronological order:
\`\`\`
## YYYY-MM-DD

- Action taken / finding noted
\`\`\`

## Cross-referencing Rules

- Use \`[[page-slug]]\` syntax to link between wiki pages
- Every entity and concept should appear in \`wiki/index.md\`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via \`related:\`
- Reflection pages reference the goals and habits reviewed during that period
- Goals link to the habits that support them via \`related:\`
- Journal entries can reference goals and reflections inline with \`[[slug]]\`

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists

## Personal Growth Conventions

- Be honest in journal and reflection entries — this wiki is for you, not an audience
- Update goal progress fields regularly; stale data is worse than no data
- Distinguish between outcome goals (what you want) and process goals (what you will do)
- Reflect on *why* habits succeed or fail, not just whether they did
- Use the synthesis directory for cross-cutting insights that span multiple goals or periods
""",

    "business": """# Wiki Schema — Business / Team

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | Papers, articles, talks, books, blog posts |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |
| meeting | wiki/meetings/ | Meeting notes, agendas, and action items |
| decision | wiki/decisions/ | Architectural or strategic decisions (ADR-style) |
| project | wiki/projects/ | Project briefs, status, and retrospectives |
| stakeholder | wiki/stakeholders/ | People, teams, and organisations involved |

## Naming Conventions

- Files: \`kebab-case.md\`
- Entities: match official name where possible (e.g., \`openai.md\`, \`gpt-4.md\`)
- Concepts: descriptive noun phrases (e.g., \`chain-of-thought.md\`)
- Sources: \`author-year-slug.md\` (e.g., \`wei-2022-cot.md\`)
- Queries: question as slug (e.g., \`does-scale-improve-reasoning.md\`)
- Meetings: \`YYYY-MM-DD-slug.md\` (e.g., \`2024-03-15-sprint-planning.md\`)
- Decisions: \`NNN-slug.md\` (e.g., \`001-adopt-typescript.md\`)
- Projects: descriptive slug (e.g., \`payments-redesign.md\`)
- Stakeholders: name or team in kebab-case (e.g., \`alice-chen.md\`, \`platform-team.md\`)

## Frontmatter

All pages must include YAML frontmatter:

\`\`\`yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
\`\`\`

Source pages also include:
\`\`\`yaml
authors: []
year: YYYY
url: ""
venue: ""
\`\`\`

Meeting pages also include:
\`\`\`yaml
date: YYYY-MM-DD
attendees: []
action_items: []
\`\`\`

Decision pages also include:
\`\`\`yaml
status: proposed | accepted | deprecated | superseded
deciders: []
date: YYYY-MM-DD
supersedes: ""   # slug of ADR this replaces, if any
\`\`\`

Project pages also include:
\`\`\`yaml
status: planned | active | on-hold | complete | cancelled
owner: ""
start_date: YYYY-MM-DD
target_date: YYYY-MM-DD
\`\`\`

## Index Format

\`wiki/index.md\` lists all pages grouped by type. Each entry:
\`\`\`
- [[page-slug]] — one-line description
\`\`\`

## Log Format

\`wiki/log.md\` records activity in reverse chronological order:
\`\`\`
## YYYY-MM-DD

- Action taken / finding noted
\`\`\`

## Cross-referencing Rules

- Use \`[[page-slug]]\` syntax to link between wiki pages
- Every entity and concept should appear in \`wiki/index.md\`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via \`related:\`
- Meeting notes reference attendees via \`attendees:\` frontmatter and \`[[stakeholder-slug]]\` links
- Decision pages link to the meetings where the decision was discussed
- Project pages link to their key decisions via \`related:\`
- Stakeholder pages list projects and decisions they are involved in

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists

## Business-Specific Conventions

- Write meeting notes during or within 24 hours — memory fades fast
- Action items must have a named owner and due date to be actionable
- Decision pages capture *context and consequences*, not just the decision itself
- Deprecated decisions should link to the decision that superseded them
- Projects should have a retrospective section added on completion
""",
}

TEMPLATE_PURPOSES = {
    "research": """# Project Purpose — Research Deep-Dive

## Research Question

<!-- State the central question this research aims to answer. Be specific and falsifiable. -->

>

## Hypothesis / Working Thesis

<!-- Your current best guess. This will evolve — update it as evidence accumulates. -->

>

## Background

<!-- What prior work or context motivates this research? What gap does it fill? -->

## Sub-questions

<!-- Break down the main question into tractable sub-questions. -->

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

<!-- How will you investigate this? What types of sources or experiments are relevant? -->

-

## Success Criteria

<!-- How will you know when you have a satisfying answer? -->

-

## Current Status

> Not started — update this section as research progresses.
""",

    "reading": """# Project Purpose — Reading

## Book Details

**Title:**
**Author:**
**Year:**
**Genre:**

## Why I'm Reading This

<!-- What drew you to this book? What do you hope to get from it? -->

## Key Themes to Track

<!-- What thematic threads do you expect or want to follow? -->

1.
2.
3.

## Questions Going In

<!-- What do you want answered or explored by the end? -->

1.
2.

## Reading Pace

**Started:**
**Target finish:**
**Current chapter:**

## First Impressions

<!-- Update after first chapter or first sitting. -->

>

## Final Takeaways

<!-- Fill in when finished. What did this book teach you? -->

>
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

<!-- High-level list — create detailed goal pages in wiki/goals/ -->

- [ ]
- [ ]
- [ ]

## Active Habits

<!-- High-level list — create detailed habit pages in wiki/habits/ -->

-
-

## Review Cadence

**Daily journal:** Yes / No
**Weekly reflection:**
**Monthly reflection:**
**Quarterly reflection:**

## Guiding Principles

<!-- What values or principles guide your growth work? -->

1.
2.
3.

## This Year's Theme

<!-- One phrase or sentence that captures your intention for the year. -->

>
""",

    "business": """# Project Purpose — Business / Team

## Business Context

**Organisation / Team:**
**Domain:**
**Time period covered:**

## Objectives

<!-- What are the top-level business objectives this wiki supports? -->

1.
2.
3.

## Key Projects

<!-- High-level list — create detailed pages in wiki/projects/ -->

-
-

## Key Stakeholders

<!-- Who are the primary people or teams involved? -->

-
-

## Open Decisions

<!-- Decisions currently in flight — create ADR pages in wiki/decisions/ -->

-
-

## Metrics / Success Criteria

<!-- How does the team measure progress toward its objectives? -->

-

## Constraints and Risks

<!-- Known constraints (budget, time, org) and risks to track -->

-

## Review Cadence

**Weekly sync notes:**
**Monthly status update:**
**Quarterly retrospective:**
""",

    "general": """# Project Purpose

## Goal

<!-- What are you trying to understand or build? -->

## Key Questions

<!-- List the primary questions driving this project -->

1.
2.
3.

## Scope

**In scope:**
-

**Out of scope:**
-

## Thesis

<!-- Your current working hypothesis or conclusion (update as the project progresses) -->

> TBD
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
    """派生 nashsu 风格的 UUID (跟 nashsu 端读到的 id 兼容)

    注: 这只是兜底, create_project_dir 内部用 uuid5 派生**真**UUID
    """
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

    # === 关键: 用模板专属 schema/purpose overwrite nashsu Rust 端写的通用版 ===
    # (按 nashsu 真实: Rust 端先写老通用, 前端 dialog.tsx:68-69 用模板 overwrite)
    # 我们后端一步到位: create_project_dir 写通用 → 然后这里 overwrite 模板专属
    schema_overwrite = target_path / 'schema.md'
    if schema_overwrite.exists() and template_data['schema_md']:
        schema_overwrite.write_text(template_data['schema_md'], encoding='utf-8')
    purpose_overwrite = target_path / 'purpose.md'
    if purpose_overwrite.exists() and template_data['purpose_md']:
        purpose_overwrite.write_text(template_data['purpose_md'], encoding='utf-8')

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
    """删知识库 (按 05-app-state.md §4 Python 代码: 改 app-state.json 4 处 + 删目录)

    不用调 19828 DELETE (nashsu 没暴露), 改 app-state.json + 文件系统
    普通用户: 只能删自己 username 下的 (按 username/kb-name 路径规则检查)
    admin: 可删任何
    """
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


