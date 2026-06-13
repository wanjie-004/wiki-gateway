"""nashsu 19828 API 客户端（httpx 异步）"""
import httpx
import sys
import json  # 2026-06-13 加: create_project_dir 注册 app-state.json 用
import time  # 2026-06-13 加: 注册时 now_ms 用
from typing import Optional, Any
from .config import settings


class NashsuAPIError(Exception):
    """nashsu API 调用错误"""
    def __init__(self, status_code: int, message: str, body: Optional[dict] = None):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"nashsu API {status_code}: {message}")


class NashsuClient:
    """nashsu 19828 API 异步客户端"""

    # 运行时 nashsu token（从 app-state.json 读, 优先级高于 settings）
    _runtime_token: Optional[str] = None

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        default_project_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.nashsu_api_base).rstrip("/")
        # token 优先级: 显式参数 > 运行时从 app-state.json 读 > settings.nashsu_token
        # 注意: NashsuClient 启动时 NashsuClient._runtime_token 可能还没初始化
        if token is not None:
            self.token = token
        else:
            self.token = NashsuClient._runtime_token or settings.nashsu_token
        self.default_project_id = default_project_id or settings.nashsu_project_id
        self.timeout = timeout

    @classmethod
    def get_token_from_app_state(cls) -> Optional[str]:
        """从 nashsu app-state.json 读真实 token (用户登录后写的)"""
        import json
        from pathlib import Path
        paths = [
            Path.home() / ".local" / "share" / "com.llmwiki.app" / "app-state.json",
            Path.home() / ".config" / "com.llmwiki.app" / "app-state.json",
            Path.home() / "Library" / "Application Support" / "com.llmwiki.app" / "app-state.json",
        ]
        for p in paths:
            if p.exists():
                try:
                    state = json.loads(p.read_text())
                    tok = state.get("apiConfig", {}).get("token", "")
                    return tok or None
                except Exception:
                    continue
        return None

    @classmethod
    def refresh_token(cls) -> Optional[str]:
        """刷新运行时 token (从 app-state.json 重读)"""
        cls._runtime_token = cls.get_token_from_app_state()
        return cls._runtime_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        auth: bool = True,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
            except httpx.RequestError as e:
                raise NashsuAPIError(
                    status_code=0,
                    message=f"连接 nashsu 失败: {e}",
                ) from e

        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", resp.text)
            except Exception:
                body = None
                msg = resp.text
            raise NashsuAPIError(status_code=resp.status_code, message=msg, body=body)

        # 204 No Content
        if resp.status_code == 204 or not resp.content:
            return {}

        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    # ============ Health ============

    async def health(self) -> dict:
        """GET /health (no auth)"""
        return await self._request("GET", "/health", auth=False)

    # ============ Projects ============

    async def list_projects(self) -> dict:
        """GET /projects"""
        return await self._request("GET", "/projects")

    # ============ Files ============

    async def list_files(
        self,
        project_id: Optional[str] = None,
        root: str = "wiki",
        recursive: bool = True,
        max_files: int = 2000,
    ) -> dict:
        """GET /projects/{id}/files"""
        pid = project_id or self.default_project_id
        return await self._request(
            "GET",
            f"/projects/{pid}/files",
            params={
                "root": root,
                "recursive": str(recursive).lower(),
                "maxFiles": max_files,
            },
        )

    async def read_file(self, project_id: Optional[str], path: str) -> dict:
        """GET /projects/{id}/files/content?path=..."""
        pid = project_id or self.default_project_id
        return await self._request(
            "GET",
            f"/projects/{pid}/files/content",
            params={"path": path},
        )

    # ============ Search ============

    async def search(
        self,
        query: str,
        top_k: int = 10,
        include_content: bool = False,
        project_id: Optional[str] = None,
    ) -> dict:
        """POST /projects/{id}/search"""
        pid = project_id or self.default_project_id
        return await self._request(
            "POST",
            f"/projects/{pid}/search",
            json_body={
                "query": query,
                "topK": top_k,
                "includeContent": include_content,
            },
        )

    # ============ Graph ============

    async def graph(
        self,
        project_id: Optional[str] = None,
        q: Optional[str] = None,
        node_type: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        """GET /projects/{id}/graph"""
        pid = project_id or self.default_project_id
        params: dict[str, Any] = {"limit": limit}
        if q:
            params["q"] = q
        if node_type:
            params["nodeType"] = node_type
        return await self._request(
            "GET",
            f"/projects/{pid}/graph",
            params=params,
        )

    # ============ Rescan ============

    async def trigger_rescan(self, project_id: Optional[str] = None) -> dict:
        """POST /projects/{id}/sources/rescan"""
        pid = project_id or self.default_project_id
        return await self._request("POST", f"/projects/{pid}/sources/rescan")


# 单例（FastAPI 用 Depends 注入更灵活，但简单场景可以直接用）
_default_client: Optional[NashsuClient] = None


def get_nashsu_client() -> NashsuClient:
    global _default_client
    if _default_client is None:
        _default_client = NashsuClient()
    return _default_client


# ============ 阶段 1 新增: 知识库管理 (kb_admin) ============

import os
import shutil
import re
import hashlib
from pathlib import Path
from typing import Optional


def sanitize_path_segment(segment: str) -> str:
    """清理路径段, 跟 nashsu `sanitizePathSegment` 类似, 防路径穿越"""
    # 禁所有危险字符
    value = re.sub(r'[<>:"\\|?*\x00-\x1f]', '_', segment)
    # 去末尾 . 和空格 (Windows)
    value = re.sub(r'[. ]+$', '', value).strip()
    if not value:
        value = '_'
    return value


def resolve_kb_path(username: str, kb_name: str) -> Path:
    """根据 username + kb_name 派生完整路径

    模式 A (settings.wiki_root_per_user=False): wiki_root_user_base/<kb_name>/
    模式 B (settings.wiki_root_per_user=True):  wiki_root_user_base/<username>/<kb_name>/
    """
    from .config import settings
    kb_name_safe = sanitize_path_segment(kb_name)
    if settings.wiki_root_per_user:
        # B: 用户独立根
        base = Path(settings.wiki_root_user_base) / sanitize_path_segment(username) / kb_name_safe
    else:
        # A: 单一根
        base = Path(settings.wiki_root_user_base) / kb_name_safe
    return base


async def create_project_dir(
    kb_name: str,
    template: dict,  # {schema_md, purpose_md, extra_dirs: [str]}
    username: str,
) -> dict:
    """创建知识库目录 + 写 schema.md + purpose.md + 6 wiki 子目录 + raw/{sources,assets} + .obsidian/3 文件

    **按 nashsu 真实 Rust 端 create_project_impl (src-tauri/src/commands/project.rs:16-242)**
    参考: https://github.com/nashsu/llm_wiki/blob/main/src-tauri/src/commands/project.rs

    路径: settings.wiki_root_user_base[/<username>]/<kb_name>/
    返回: {path: str, project_id: str, registered: bool, schema_written: bool,
           purpose_written: bool, extra_dirs_created: [str]}

    关键 (2026-06-13 修): 必须建 raw/sources/ 目录, 否则 nashsu 19828 读 raw/sources 报
    "No such file or directory (os error 2)" (来源: 测试 test-wanjie004-kb 浏览源文件报错)

    关键 (2026-06-13 修 2): 必须同步注册到 nashsu app-state.json 的 projectRegistry,
    否则 nashsu 19828 /projects 列表看不到新项目 (nashsu 启动时一次性加载 projectRegistry,
    改 app-state.json 后 19828 实时读, 不用重启) (来源: 同上测试)

    关键 (2026-06-13 修 3 — 按 nashsu 真实 Rust 端):
    - Rust 端**写死** 7-type 通用 schema.md (没模板概念)
    - 前端 (React dialog) 用**模板 overwrite** schema.md/purpose.md + 建 extra_dirs
    - 但 Rust 端**先**建 8 个目录 (raw/{sources,assets} + 6 wiki) + 5 个 wiki 文件
    - **再**建 .obsidian/3 配置文件 (Obsidian 兼容)
    - 顺序: dirs → schema.md → purpose.md → index.md → log.md → overview.md → .obsidian
    """
    from .config import settings
    target = resolve_kb_path(username, kb_name)
    target.mkdir(parents=True, exist_ok=False)  # 冲突 → FileExistsError

    # === 步骤 1: 建 8 个目录 (按 nashsu project.rs:24-37) ===
    # raw/sources (Source Watch 监听) + raw/assets (Obsidian attachment 目录)
    # + 6 wiki 子目录 (对应 7 base type, overview 是顶层文件不是目录)
    base_dirs = [
        'raw/sources',                           # nashsu 19828 Source Watch 目标
        'raw/assets',                            # Obsidian 附件目录 (nashsu app.json attachmentFolderPath)
        'wiki/entities',                         # entity page type
        'wiki/concepts',                         # concept
        'wiki/sources',                          # source
        'wiki/queries',                          # query
        'wiki/comparisons',                      # comparison
        'wiki/synthesis',                        # synthesis
    ]
    created = []
    for d in base_dirs:
        sub = target / d
        sub.mkdir(parents=True, exist_ok=True)
        created.append(d)

    # === 步骤 2: 写 schema.md (按 nashsu project.rs:42-119) — 写死 7 type 通用 ===
    # 注: 前端 dialog 会用模板**覆盖**这文件 (dialog.tsx:68), 这里写**通用版**
    # 7 type 含 overview (在 wiki/ 顶层, 不是子目录 — 跟 nashsu BASE_SCHEMA_TYPES 一致)
    schema_content = """# Wiki Schema

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

- Files: `kebab-case.md`
- Entities: match official name where possible (e.g., `gpt-4.md`, `openai.md`)
- Concepts: descriptive noun phrases (e.g., `chain-of-thought.md`)
- Sources: `author-year-slug.md` (e.g., `wei-2022-chain-of-thought.md`)
- Queries: question as slug (e.g., `does-scale-improve-reasoning.md`)

## Frontmatter

All pages must include YAML frontmatter:

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

Source pages also include:
```yaml
authors: []
year: YYYY
url: ""
venue: ""
```

## Index Format

`wiki/index.md` lists all pages grouped by type. Each entry:
```
- [[page-slug]] — one-line description
```

## Log Format

`wiki/log.md` records research activity in reverse chronological order:
```
## YYYY-MM-DD

- Action taken / finding noted
```

## Cross-referencing Rules

- Use `[[page-slug]]` syntax to link between wiki pages
- Every entity and concept should appear in `wiki/index.md`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via `related:`

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists
"""
    (target / 'schema.md').write_text(schema_content, encoding='utf-8')

    # === 步骤 3: 写 purpose.md (按 nashsu project.rs:121-152) — 写死通用 ===
    purpose_content = """# Project Purpose

## Goal

<!-- What are you trying to understand or build? -->

## Key Questions

<!-- List the primary questions driving this research -->

1.
2.
3.

## Scope

<!-- What is in scope? What is explicitly out of scope? -->

**In scope:**
-

**Out of scope:**
-

## Thesis

<!-- Your current working hypothesis or conclusion (update as research progresses) -->

> TBD
"""
    (target / 'purpose.md').write_text(purpose_content, encoding='utf-8')

    today = time.strftime('%Y-%m-%d')

    # === 步骤 4: 写 wiki/index.md (按 nashsu project.rs:154-169) — 无 frontmatter ===
    # ⚠️ nashsu 实际**没** frontmatter, 6 H2 节 (无 ## Overviews 单独节)
    index_content = """# Wiki Index

## Entities

## Concepts

## Sources

## Queries

## Comparisons

## Synthesis
"""
    (target / 'wiki').mkdir(exist_ok=True)
    (target / 'wiki' / 'index.md').write_text(index_content, encoding='utf-8')

    # === 步骤 5: 写 wiki/log.md (按 nashsu project.rs:171-180) — 无 frontmatter ===
    # ⚠️ nashsu 实际**没** frontmatter, `# Research Log` (不是 `# <kb_name> — Log`)
    log_content = f"""# Research Log

## {today}

- Project created
"""
    (target / 'wiki' / 'log.md').write_text(log_content, encoding='utf-8')

    # === 步骤 6: 写 wiki/overview.md (按 nashsu project.rs:182-194) — 有 frontmatter ===
    # ⚠️ nashsu 实际**只**有 type/title/tags/related 4 字段 (没 sources/created/updated)
    overview_content = """---
type: overview
title: Project Overview
tags: []
related: []
---

# Overview

<!-- Provide a high-level summary of what this wiki covers and its current state. Update regularly as understanding deepens. -->
"""
    (target / 'wiki' / 'overview.md').write_text(overview_content, encoding='utf-8')

    # === 步骤 7: 建 .obsidian/ + 3 配置文件 (按 nashsu project.rs:196-235) — Obsidian 兼容 ===
    (target / '.obsidian').mkdir(exist_ok=True)

    # app.json: attachmentFolderPath + userIgnoreFilters + useMarkdownLinks
    obsidian_app_config = """{
  "attachmentFolderPath": "raw/assets",
  "userIgnoreFilters": [
    ".cache",
    ".llm-wiki",
    ".superpowers"
  ],
  "useMarkdownLinks": false,
  "newLinkFormat": "shortest",
  "showUnsupportedFiles": false
}"""
    (target / '.obsidian' / 'app.json').write_text(obsidian_app_config, encoding='utf-8')

    # appearance.json: dark mode + baseFontSize
    obsidian_appearance = """{
  "baseFontSize": 16,
  "theme": "obsidian"
}"""
    (target / '.obsidian' / 'appearance.json').write_text(obsidian_appearance, encoding='utf-8')

    # core-plugins.json: 启用 graph + backlinks 等
    obsidian_core_plugins = """{
  "file-explorer": true,
  "global-search": true,
  "graph": true,
  "backlink": true,
  "tag-pane": true,
  "page-preview": true,
  "outgoing-link": true,
  "starred": true
}"""
    (target / '.obsidian' / 'core-plugins.json').write_text(obsidian_core_plugins, encoding='utf-8')
    created.append('.obsidian')

    # === 步骤 8: 模板 extra_dirs (按 nashsu dialog.tsx:70-72) ===
    # 前端 dialog 也会调 createDirectory, 但保险起见后端也建 (避免前端失败留下半成品)
    # ⚠️ 这是 nashsu **没有**的额外保险, 但 wiki-gateway 是 server-side, 加上更稳
    for d in template.get('extra_dirs', []):
        sub = target / d
        sub.mkdir(parents=True, exist_ok=True)
        created.append(d)

    # === 步骤 9: 同步注册到 nashsu app-state.json (按 nashsu upsertProjectInfo) ===
    project_id = ''
    registered = False
    state_path = Path.home() / '.local' / 'share' / 'com.llmwiki.app' / 'app-state.json'
    if state_path.exists():
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            registry = state.setdefault('projectRegistry', {})

            # 派生 project_id: 按 nashsu UUID 风格 (4b7284fc-8528-44b3-8a02-7fd3cb6f36db)
            # 用 uuid5(namespace + path) 派生稳定 UUID (跟 path 一致)
            import uuid as _uuid
            namespace = _uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # UUID NAMESPACE_URL
            project_uuid = _uuid.uuid5(namespace, f'wiki-gateway/{username}/{kb_name}/{str(target)}')
            project_id = str(project_uuid)

            # 注册
            now_ms = int(time.time() * 1000)
            registry[project_id] = {
                'name': kb_name,
                'path': str(target),
                'lastOpened': now_ms,
                'createdAt': now_ms,
            }

            # 加到 recentProjects (去重)
            recent = state.setdefault('recentProjects', [])
            recent = [r for r in recent if r.get('id') != project_id]
            recent.insert(0, {'id': project_id, 'name': kb_name, 'path': str(target)})
            state['recentProjects'] = recent

            # 设 lastProject (新项目切为当前)
            state['lastProject'] = {'id': project_id, 'name': kb_name, 'path': str(target)}

            # 写回
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            registered = True
        except Exception as e:
            # 不让注册失败阻塞创建 (降级)
            registered = False

    return {
        'path': str(target),
        'project_id': project_id,
        'registered': registered,
        'schema_written': True,
        'purpose_written': True,
        'extra_dirs_created': created,
    }


async def delete_project_state(project_id: str) -> dict:
    """删知识库 (按 05-app-state.md §4 Python 代码: 改 app-state.json 4 处 + 删目录)

    不用调 19828 (nashsu 没暴露 DELETE API), 直接改 19828 端 store + 文件系统
    """
    from .config import settings
    # 找 app-state.json 路径
    candidates = [
        Path.home() / '.local' / 'share' / 'com.llmwiki.app' / 'app-state.json',
        Path.home() / '.config' / 'com.llmwiki.app' / 'app-state.json',
        Path.home() / 'Library' / 'Application Support' / 'com.llmwiki.app' / 'app-state.json',
    ]
    app_state_path = None
    for p in candidates:
        if p.exists():
            app_state_path = p
            break

    if not app_state_path:
        return {'deleted_app_state': False, 'reason': 'app-state.json not found'}

    import json
    with open(app_state_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 找 project path (从 projectRegistry)
    project_path = None
    if 'projectRegistry' in data and project_id in data['projectRegistry']:
        project_path = data['projectRegistry'][project_id].get('path')

    # 删 4 处
    if 'projectRegistry' in data and project_id in data['projectRegistry']:
        del data['projectRegistry'][project_id]
    if 'projectOutputLanguages' in data and project_id in data['projectOutputLanguages']:
        del data['projectOutputLanguages'][project_id]
    if 'sourceWatchConfig' in data and project_id in data['sourceWatchConfig']:
        del data['sourceWatchConfig'][project_id]
    if 'recentProjects' in data:
        data['recentProjects'] = [p for p in data['recentProjects'] if p.get('id') != project_id]

    # 删 scheduledImportConfig (key 含 path)
    if project_path:
        sched_key = f'scheduledImportConfig:{project_path}'
        if sched_key in data:
            del data[sched_key]

    # lastProject 兜底
    if data.get('lastProject', {}).get('id') == project_id:
        fallback = data['recentProjects'][0] if data['recentProjects'] else None
        if fallback:
            data['lastProject'] = {'id': fallback['id'], 'name': fallback['name'], 'path': fallback['path']}

    # 写回
    with open(app_state_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 删目录
    dir_deleted = False
    if project_path:
        p = Path(project_path)
        if p.exists():
            shutil.rmtree(p)
            dir_deleted = True

    return {
        'deleted_app_state': True,
        'deleted_dir': dir_deleted,
        'project_path': project_path,
    }


async def upload_source_file(project_id: str, file_path: str, content: bytes) -> dict:
    """上传源文件到 <project>/raw/sources/<file_path>

    file_path 相对于 raw/sources/, 例: "AI/foo.md"
    nashsu Source Watch 自动检测并 ingest
    """
    from .config import settings
    # 找项目路径
    candidates = [
        Path.home() / '.local' / 'share' / 'com.llmwiki.app' / 'app-state.json',
    ]
    project_path = None
    for p in candidates:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                import json as _json
                d = _json.load(f)
            if d.get('projectRegistry', {}).get(project_id, {}).get('path'):
                project_path = Path(d['projectRegistry'][project_id]['path'])
            break

    if not project_path:
        return {'uploaded': False, 'reason': 'project not found'}

    # 路径安全
    safe_rel = file_path
    for bad in ['..', '~']:
        if bad in safe_rel:
            return {'uploaded': False, 'reason': f'illegal path: {bad}'}

    target = project_path / 'raw' / 'sources' / safe_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    return {
        'uploaded': True,
        'path': str(target),
        'size': len(content),
    }


async def delete_source_file(project_id: str, file_path: str) -> dict:
    """删源文件 <project>/raw/sources/<file_path>

    nashsu Source Watch 检测到删除自动 cleanup wiki 页
    """
    candidates = [
        Path.home() / '.local' / 'share' / 'com.llmwiki.app' / 'app-state.json',
    ]
    project_path = None
    for p in candidates:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                import json as _json
                d = _json.load(f)
            if d.get('projectRegistry', {}).get(project_id, {}).get('path'):
                project_path = Path(d['projectRegistry'][project_id]['path'])
            break

    if not project_path:
        return {'deleted': False, 'reason': 'project not found'}

    target = project_path / 'raw' / 'sources' / file_path
    if not target.exists():
        return {'deleted': False, 'reason': 'file not found'}

    target.unlink()
    return {'deleted': True, 'path': str(target)}


async def get_ingest_status(project_id: str) -> dict:
    """查 ingest 状态 (读 .llm-wiki/file-change-queue.json + wiki/log.md 最新)

    不用调 19828 (nashsu 没暴露 file-change-queue 端点), 直接读文件系统
    """
    candidates = [
        Path.home() / '.local' / 'share' / 'com.llmwiki.app' / 'app-state.json',
    ]
    project_path = None
    for p in candidates:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                import json as _json
                d = _json.load(f)
            if d.get('projectRegistry', {}).get(project_id, {}).get('path'):
                project_path = Path(d['projectRegistry'][project_id]['path'])
            break

    if not project_path:
        return {'status': 'unknown', 'reason': 'project not found'}

    queue_file = project_path / '.llm-wiki' / 'file-change-queue.json'
    snapshot_file = project_path / '.llm-wiki' / 'file-snapshot.json'
    log_file = project_path / 'wiki' / 'log.md'

    status = {
        'pending': 0,
        'processing': 0,
        'done': 0,
        'failed': 0,
        'queue_size': 0,
        'last_log': None,
    }

    if queue_file.exists():
        try:
            with open(queue_file, 'r', encoding='utf-8') as f:
                queue = json.load(f)
            tasks = queue.get('tasks', [])
            status['queue_size'] = len(tasks)
            for t in tasks:
                s = t.get('status', 'pending')
                if s in status:
                    status[s] += 1
        except Exception as e:
            status['queue_error'] = str(e)

    if log_file.exists():
        try:
            lines = log_file.read_text(encoding='utf-8').split('\n')
            for line in lines:
                if 'ingest' in line.lower():
                    status['last_log'] = line.strip()
                    break
        except Exception:
            pass

    return status


async def ingest_file(project_id: str, file_path: str) -> dict:
    """强制 ingest 单文件 (调 rescan 触发全队列, nashsu 没暴露单文件 ingest)

    文档: 09-source-watch-flow.md §10.1 路径 A (手动 UI 按钮) — 走 rescan
    """
    return await trigger_rescan(project_id)

