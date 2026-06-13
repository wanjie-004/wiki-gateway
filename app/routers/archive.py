"""Archive 路由（归档优秀结果到 wiki）"""
import re
import json
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..models import ArchiveRequest, ArchiveResponse
from ..config import settings
from ..nashsu_client import NashsuClient, get_nashsu_client, NashsuAPIError

router = APIRouter(prefix="/api", tags=["archive"])


# 允许的归档目标目录（白名单防越权）
ALLOWED_DIRS = {"synthesis", "concepts", "queries", "sources"}


def sanitize_filename(name: str) -> str:
    """生成安全的文件名（去特殊字符）"""
    # 保留中文、字母、数字、- _ （）
    safe = re.sub(r'[\\/*?:"<>|\r\n]', "_", name)
    safe = safe.strip(". ")
    if not safe:
        safe = "untitled"
    return safe[:80]


def build_frontmatter(
    user: dict,
    title: str,
    tags: list[str],
    source: str | None,
    note: str | None,
) -> str:
    """构造 YAML frontmatter"""
    fm_lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"type: synthesis",
        f"created: {datetime.now().isoformat()}",
        f"updated: {datetime.now().isoformat()}",
        f"archived_by: {json.dumps(user['username'], ensure_ascii=False)}",
    ]
    if tags:
        fm_lines.append(f"tags: {json.dumps(tags, ensure_ascii=False)}")
    if source:
        fm_lines.append(f"source: {json.dumps(source, ensure_ascii=False)}")
    if note:
        fm_lines.append(f"archive_note: {json.dumps(note, ensure_ascii=False)}")
    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines)


@router.post("/archive", response_model=ArchiveResponse, status_code=status.HTTP_201_CREATED)
async def archive(
    req: ArchiveRequest,
    user: dict = Depends(get_current_user),
    nashsu: NashsuClient = Depends(get_nashsu_client),
):
    """归档优秀结果到 wiki 目录

    流程：
    1. 校验 target_dir（白名单）
    2. 构造文件名（按日期 + 标题）
    3. 写文件到 wiki/{target_dir}/
    4. 触发 nashsu 重新摄取（如果 trigger_rescan=true）
    """
    # 1. 校验 target_dir
    target_dir = req.target_dir.strip().strip("/")
    if target_dir not in ALLOWED_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"target_dir 必须是以下之一: {sorted(ALLOWED_DIRS)}",
        )

    # 2. 构造文件路径
    wiki_root = Path(settings.nashsu_wiki_root)
    target_path_dir = wiki_root / "wiki" / target_dir
    target_path_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    safe_title = sanitize_filename(req.title)
    filename = f"{date_str}_归档_{safe_title}.md"
    target_file = target_path_dir / filename

    # 避免重名
    counter = 1
    while target_file.exists():
        filename = f"{date_str}_归档_{safe_title}_{counter}.md"
        target_file = target_path_dir / filename
        counter += 1

    # 3. 构造内容（frontmatter + 内容）
    frontmatter = build_frontmatter(user, req.title, req.tags, req.source, req.note)
    body = req.content.strip()
    if not body.startswith("---"):
        full_content = frontmatter + "\n" + body + "\n"
    else:
        # 用户自己写了 frontmatter，保留
        full_content = body + "\n"

    # 4. 写文件
    try:
        target_file.write_text(full_content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"写文件失败: {e}",
        )

    size = target_file.stat().st_size
    rel_path = str(target_file.relative_to(wiki_root))

    # 5. 触发 rescan
    rescan_result = None
    rescan_triggered = False
    if req.trigger_rescan:
        try:
            rescan_result = await nashsu.trigger_rescan()
            rescan_triggered = True
        except NashsuAPIError as e:
            # rescan 失败不影响归档本身成功
            rescan_result = {"error": e.message, "status_code": e.status_code}

    return ArchiveResponse(
        path=rel_path,
        absolute_path=str(target_file),
        size_bytes=size,
        rescan_triggered=rescan_triggered,
        rescan_result=rescan_result,
    )
