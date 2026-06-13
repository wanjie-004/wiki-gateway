"""RAG 上下文组装 — 7 phase 翻译 nashsu chat-panel.tsx

nashsu 流程（精简）：
  Phase 1: searchWiki  → top 10 命中
  Phase 2: graph 1-level 扩展 (relevance >= 2.0)
  Phase 3: index 修剪 (按 token 相关性)
  Phase 4: 页面预算分配 (PAGE_BUDGET = maxContextSize * 60%)
  Phase 5: 累加页面 (P0 titleMatch → P1 content → P2 graph → P3 overview)
  Phase 6: 构造 system prompt
  Phase 7: language reminder 注入

差异：
- 没有 nashsu 那种 web search / anytxt 外部源（本版本）
- 复用 wiki-gateway 已有的 nashsu_client（search/files/read/graph）
"""
import json
import re
import logging
from typing import List, Dict, Optional, Tuple, Set, Any

from ..nashsu_client import get_nashsu_client
from ..schemas.chat import LlmConfig, MessageReference


logger = logging.getLogger(__name__)


# ============ Budget ============

def compute_context_budget(max_context_size: int) -> Dict[str, int]:
    """对标 nashsu computeContextBudget
    60% wiki pages / 20% chat history / 5% index / 15% system headroom
    """
    # 用 chars 近似（1 token ≈ 4 chars）
    return {
        "page_budget": int(max_context_size * 0.60 * 4),
        "index_budget": int(max_context_size * 0.05 * 4),
        "max_page_size": int(max_context_size * 0.15 * 4),
        "headroom_reserve": int(max_context_size * 0.15 * 4),
    }


# ============ Phase 1: Search ============

async def phase1_search(project_id: Optional[str], query: str, top_k: int = 10) -> List[Dict]:
    """对标 nashsu searchWiki → top 10"""
    client = get_nashsu_client()
    print(f"🔍 [RAG] phase1_search project_id={project_id!r} query={query!r} top_k={top_k}")
    print(f"   client.token: {(client.token or '')[:20]}... (len={len(client.token or '')})")
    try:
        resp = await client.search(query=query, top_k=top_k, project_id=project_id)
        results = resp.get("results", []) or []
        print(f"   ✅ got {len(results)} results")
        for r in results[:5]:
            print(f"      - {r.get('path')}, score={r.get('score')}, titleMatch={r.get('titleMatch')}")
        return results
    except Exception as e:
        print(f"   ❌ phase1_search EXCEPTION: {type(e).__name__}: {e}")
        return []
    except Exception as e:
        logger.warning("phase1 search failed: %s", e)
        return []


# ============ Phase 2: Graph 1-Level Expansion ============

async def phase2_graph_expand(
    project_id: Optional[str], top_results: List[Dict], limit_per_node: int = 3
) -> List[Dict]:
    """对标 nashsu getRelatedNodes(limit=3, relevance>=2.0)

    完整移植 nashsu 4-signal relevance 计算:
    1. Direct links (3.0/边)
    2. Source overlap (4.0/shared source) — 需要 frontmatter.sources
    3. Adamic-Adar common neighbors (1.5)
    4. Type affinity (1.0)

    Returns: [{title, path, relevance}, ...]
    """
    if not top_results:
        return []
    client = get_nashsu_client()
    try:
        graph = await client.graph(project_id, limit=200)
    except Exception as e:
        logger.warning("phase2 graph failed: %s", e)
        return []

    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    print(f"🔍 [RAG] phase2_graph: got {len(nodes)} nodes, {len(edges)} edges")

    # 建邻接表 + outLinks/inLinks maps (用 edge source/target)
    adj: Dict[str, List[Tuple[str, float]]] = {}
    out_links: Dict[str, Set[str]] = {nid: set() for nid in nodes}
    in_links: Dict[str, Set[str]] = {nid: set() for nid in nodes}
    for e in edges:
        s, t = e["source"], e["target"]
        if s not in nodes or t not in nodes:
            continue
        w = float(e.get("weight", 1.0))
        adj.setdefault(s, []).append((t, w))
        adj.setdefault(t, []).append((s, w))
        out_links[s].add(t)
        in_links[t].add(s)

    # 读每个 node 的 .md frontmatter 拿 sources + type
    # 用 nashsu 的 read_file 读 .md, 然后 Python 解析 frontmatter
    node_sources: Dict[str, List[str]] = {}
    node_types: Dict[str, str] = {}
    for nid, node in nodes.items():
        path = node.get("path", "")
        # type default = "other" (对齐 nashsu extractFrontmatter)
        if not path:
            continue
        try:
            content_resp = await client.read_file(project_id, path)
            content = content_resp.get("content", "")
            # 解析 frontmatter
            fm = _parse_frontmatter(content)
            node_sources[nid] = fm.get("sources", [])
            node_types[nid] = fm.get("type", "other")  # nashsu default = "other"
        except Exception:
            node_sources[nid] = []
            node_types[nid] = "other"

    # 4-signal relevance 计算 (对每个 top_result 节点, 算它和所有邻居的 relevance)
    seen = set()
    expansions = []
    search_paths = {r["path"] for r in top_results}

    def calc_relevance(src_id: str, tgt_id: str) -> float:
        if src_id == tgt_id or tgt_id not in nodes:
            return 0.0
        # Signal 1: Direct links (3.0/边)
        direct = 0
        if tgt_id in out_links.get(src_id, set()):
            direct += 1
        if src_id in out_links.get(tgt_id, set()):
            direct += 1
        direct_score = direct * 3.0

        # Signal 2: Source overlap (4.0)
        src_sources = set(node_sources.get(src_id, []))
        shared = sum(1 for s in node_sources.get(tgt_id, []) if s in src_sources)
        source_score = shared * 4.0

        # Signal 3: Adamic-Adar common neighbors (1.5)
        nbrs_a = out_links.get(src_id, set()) | in_links.get(src_id, set())
        nbrs_b = out_links.get(tgt_id, set()) | in_links.get(tgt_id, set())
        aa = 0.0
        for n in nbrs_a & nbrs_b:
            deg = len(out_links.get(n, set()) | in_links.get(n, set()))
            if deg > 1:
                import math
                aa += 1.0 / math.log(max(deg, 2))
        common_score = aa * 1.5

        # Signal 4: Type affinity (1.0)
        t_a = node_types.get(src_id, "concept")
        t_b = node_types.get(tgt_id, "concept")
        affinity = TYPE_AFFINITY.get(t_a, {}).get(t_b, 0.5)
        affinity_score = affinity * 1.0

        return direct_score + source_score + common_score + affinity_score

    for r in top_results:
        path = r["path"]
        fname = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]  # filename without .md
        # 算 fname 与所有节点的 relevance
        scored = []
        for tgt_id in nodes:
            if tgt_id == fname:
                continue
            rel = calc_relevance(fname, tgt_id)
            if rel >= 2.0:
                scored.append((tgt_id, rel))
        scored.sort(key=lambda x: x[1], reverse=True)
        # nashsu 拿 top 3 per node
        for tgt_id, rel in scored[:limit_per_node]:
            if tgt_id in seen:
                continue
            target_node = nodes.get(tgt_id)
            if not target_node:
                continue
            target_path = target_node.get("path", "")
            if target_path in search_paths:
                continue
            seen.add(tgt_id)
            expansions.append({
                "title": target_node.get("label", tgt_id),
                "path": target_path,
                "relevance": rel,
            })
    expansions.sort(key=lambda x: x["relevance"], reverse=True)
    print(f"   ✅ got {len(expansions)} graph expansions")
    for e in expansions[:15]:
        print(f"      - {e['path']} (rel={e['relevance']:.2f})")
    return expansions


# TYPE_AFFINITY (完整移植 nashsu graph-relevance.ts)
TYPE_AFFINITY: Dict[str, Dict[str, float]] = {
    "entity":    {"concept": 1.2, "entity": 0.8, "source": 1.0, "synthesis": 1.0, "query": 0.8},
    "concept":   {"entity": 1.2, "concept": 0.8, "source": 1.0, "synthesis": 1.2, "query": 1.0},
    "source":    {"entity": 1.0, "concept": 1.0, "source": 0.5, "query": 0.8, "synthesis": 1.0},
    "query":     {"concept": 1.0, "entity": 0.8, "synthesis": 1.0, "source": 0.8, "query": 0.5},
    "synthesis": {"concept": 1.2, "entity": 1.0, "source": 1.0, "query": 1.0, "synthesis": 0.8},
}


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """完整 frontmatter 解析 — 移植 nashsu extractFrontmatter

    支持的 list 格式:
    - [a, b, c]                  (无引号)
    - ["a", "b", "c"]            (双引号)
    - ['a', 'b', 'c']            (单引号)
    - [raw/sources/x.md]         (路径无引号)
    - block 格式:                (多行)
        sources:
          - "a.md"
          - "b.md"
    """
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end < 0:
        return {}
    fm_block = content[3:end].strip()
    fm: Dict[str, Any] = {}

    # 用正则处理 block 格式 (key:\n  - item)
    block_pattern = re.compile(r"^(\w+):\s*\n((?:\s+-\s+.+\n?)+)", re.MULTILINE)
    consumed_keys = set()
    for m in block_pattern.finditer(fm_block):
        k = m.group(1)
        list_block = m.group(2)
        items = []
        for line in list_block.strip().split("\n"):
            # 提取 "- xxx" 中的 xxx
            item_match = re.match(r"\s+-\s+[\"']?(.+?)[\"']?\s*$", line)
            if item_match:
                items.append(item_match.group(1))
        fm[k] = items
        consumed_keys.add(k)

    for line in fm_block.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k in consumed_keys:
            continue
        v = v.strip().strip('"').strip("'")
        # 解析 inline list 格式
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1]
            items = []
            for s in inner.split(","):
                s = s.strip().strip('"').strip("'")
                if s:
                    items.append(s)
            fm[k] = items
        else:
            fm[k] = v
    return fm


# ============ Phase 3: Trim Index by Relevance ============

def _tokenize(text: str) -> List[str]:
    """简单分词：英文按 \W+，中文按字"""
    text = text.lower()
    # 英文词
    en_tokens = re.findall(r"[a-z0-9]+", text)
    # 单字（中文字符单独切分）
    cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return en_tokens + cn_chars


def phase3_trim_index(raw_index: str, query: str, budget: int) -> str:
    """对标 nashsu index trim
    保留 header + 含 query token 的行
    """
    if len(raw_index) <= budget:
        return raw_index
    tokens = set(_tokenize(query))
    lines = raw_index.split("\n")
    kept = []
    used = 0
    for line in lines:
        is_header = line.startswith("#")
        is_relevant = any(t in line.lower() for t in tokens)
        if is_header or is_relevant:
            if used + len(line) + 1 <= budget:
                kept.append(line)
                used += len(line) + 1
    result = "\n".join(kept)
    if len(result) < len(raw_index):
        result += "\n\n[...index trimmed to relevant entries...]"
    return result


# ============ Phase 4-5: Add Pages Within Budget ============

async def phase4_5_add_pages(
    project_id: Optional[str],
    top_results: List[Dict],
    graph_expansions: List[Dict],
    budget: Dict[str, int],
) -> List[Dict]:
    """对标 nashsu tryAddPage（按 P0→P1→P2→P3 优先级）"""
    client = get_nashsu_client()
    page_budget = budget["page_budget"]
    max_page_size = budget["max_page_size"]
    used = 0
    pages: List[Dict] = []

    async def try_add(title: str, path: str, priority: int) -> bool:
        nonlocal used
        if used >= page_budget:
            return False
        try:
            content = await client.read_file(project_id, path)
        except Exception as e:
            logger.warning(f"RAG try_add failed [{path}]: {type(e).__name__}: {e}")
            return False
        text = content.get("content", "")
        if not text:
            return False
        truncated = (
            text[:max_page_size] + "\n\n[...truncated...]"
            if len(text) > max_page_size
            else text
        )
        if used + len(truncated) > page_budget:
            return False
        used += len(truncated)
        pages.append({"title": title, "path": path, "content": truncated, "priority": priority})
        return True

    # P0: titleMatch
    for r in [r for r in top_results if r.get("titleMatch")]:
        if not await try_add(r.get("title", r["path"]), r["path"], 0):
            break
    # P1: content match
    for r in [r for r in top_results if not r.get("titleMatch")]:
        if not await try_add(r.get("title", r["path"]), r["path"], 1):
            break
    # P2: graph expansions
    for e in graph_expansions:
        if not await try_add(e["title"], e["path"], 2):
            break
    # P3: overview fallback
    if not pages:
        try:
            await try_add("Overview", "wiki/overview.md", 3)
        except Exception:
            pass
    return pages


# ============ Phase 6: System Prompt ============

def build_system_prompt(
    pages: List[Dict],
    index_md: str,
    purpose_md: str,
    query: str,
    project_name: str,
) -> str:
    """对标 nashsu 系统 prompt 构造（精简版，不含 web/anytxt 外部源）"""
    pages_ctx = (
        "\n\n---\n\n".join(
            f"### [{i+1}] {p['title']}\nPath: {p['path']}\n\n{p['content']}"
            for i, p in enumerate(pages)
        )
        if pages else "(No wiki pages found)"
    )
    page_list = "\n".join(f"[{i+1}] {p['title']} ({p['path']})" for i, p in enumerate(pages))
    out_lang = "Chinese"  # 简化版，固定中文输出

    parts = [
        "You are a knowledgeable wiki assistant. Answer questions based on the wiki content provided below.",
        "",
        "## Rules",
        "- Answer based ONLY on the numbered wiki pages provided below.",
        "- If the provided pages don't contain enough information, say so honestly.",
        "- Use [[wikilink]] syntax to reference wiki pages.",
        "- When citing information, use the page number in brackets, e.g. [1], [2].",
        "- At the VERY END of your response, add a hidden comment listing which page numbers you used:",
        "  <!-- cited: 1, 3, 5 -->",
        "",
        "Use markdown formatting for clarity.",
        "",
        purpose_md and f"## Wiki Purpose\n{purpose_md}",
        index_md and f"## Wiki Index\n{index_md}",
        pages and f"## Page List\n{page_list}",
        f"## Wiki Pages\n\n{pages_ctx}",
        "",
        "---",
        "",
        f"## ⚠️ MANDATORY OUTPUT LANGUAGE: {out_lang}",
        "",
        f"You MUST write your entire response in **{out_lang}**.",
        f"The wiki content above may be in a different language, but this is IRRELEVANT to your output language.",
        f"Ignore the language of the wiki content. Write in {out_lang} only.",
        f"DO NOT use any other language. This overrides all other instructions.",
    ]
    return "\n".join(p for p in parts if p)


# ============ Main Entry ============

async def build_rag_context(
    project_id: Optional[str],
    query: str,
    llm_cfg: LlmConfig,
) -> Tuple[str, List[MessageReference]]:
    """完整 RAG 流程入口

    Returns:
        (system_prompt, references)
    """
    budget = compute_context_budget(llm_cfg.maxContextSize)
    client = get_nashsu_client()

    # 读 wiki index + purpose (nashsu: Promise.all)
    try:
        files = await client.list_files(project_id, "wiki", recursive=False, max_files=10)
        # 找到 index.md 和 purpose.md 的路径
        index_path = None
        purpose_path = None
        def walk(nodes):
            nonlocal index_path, purpose_path
            for n in nodes:
                if n.get("isDir"):
                    walk(n.get("children") or [])
                else:
                    name = n.get("name", "")
                    p = n.get("path", "")
                    if name == "index.md" and not index_path:
                        index_path = p
                    elif name == "purpose.md" and not purpose_path:
                        purpose_path = p
        walk(files.get("files", []))
        if not index_path:
            index_path = "wiki/index.md"
        if not purpose_path:
            purpose_path = "purpose.md"
        try:
            index_md = (await client.read_file(project_id, index_path)).get("content", "")
        except Exception:
            index_md = ""
        try:
            purpose_md = (await client.read_file(project_id, purpose_path)).get("content", "")
        except Exception:
            purpose_md = ""
    except Exception as e:
        logger.warning("read wiki root files failed: %s", e)
        index_md = ""
        purpose_md = ""

    # Phase 1 — topK=20 (nashsu search.ts default), but use only first 10 (nashsu chat-panel.tsx:236)
    all_search_results = await phase1_search(project_id, query, top_k=20)
    top_results = all_search_results[:10]

    # Phase 2
    graph_expansions = await phase2_graph_expand(project_id, top_results, limit_per_node=3)

    # Phase 3
    index_trimmed = phase3_trim_index(index_md, query, budget["index_budget"])

    # Phase 4-5
    pages = await phase4_5_add_pages(project_id, top_results, graph_expansions, budget)

    # Phase 6
    system_prompt = build_system_prompt(
        pages=pages,
        index_md=index_trimmed,
        purpose_md=purpose_md,
        query=query,
        project_name="(wiki project)",
    )

    # 收集 references
    references = [
        MessageReference(
            title=p["title"],
            path=p["path"],
            kind="wiki",
        )
        for p in pages
    ]
    return system_prompt, references
