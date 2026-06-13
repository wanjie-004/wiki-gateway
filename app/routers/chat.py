"""Chat router — POST /api/chat (SSE) + 持久化

对标 nashsu 整套 chat pipeline，全部搬到 wiki-gateway 后端
"""
import asyncio
import json
import time
import uuid
import logging
from typing import List, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text as sql_text  # may not exist; will use sqlite3 instead

from ..auth import get_current_user
from ..schemas.chat import (
    ChatRequest,
    DisplayMessage,
    Conversation,
    MessageReference,
)
from ..services.rag import build_rag_context
from ..services.llm_client import stream_chat
from ..db import get_connection
from .projects import get_user_project_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ============ Schema Init ============

def init_chat_db():
    """创建 chat 持久化表（启动时调用）"""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_id INTEGER,
                title TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                references_json TEXT,
                project_id TEXT,  -- 产生这条消息的 project (前端历史回放能显示)
                timestamp INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
                ON chat_messages(conversation_id, timestamp);
        """)
        # 兼容老 schema: 旧 chat_messages 表没 project_id 列
        # 用 PRAGMA 检查后再 ALTER (避免重复加列报错)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
        if "project_id" not in cols:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN project_id TEXT")
            logger.info("migrated chat_messages: added project_id column")
        conn.commit()
    finally:
        conn.close()


# ============ Helpers ============

def _gen_conv_id() -> str:
    return f"conv_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _gen_msg_id() -> str:
    return f"msg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _save_conversation(conv: Conversation, project_id: str, user_id: int):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO chat_conversations
            (id, project_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (conv.id, project_id, user_id, conv.title, conv.createdAt, conv.updatedAt),
        )
        conn.commit()
    finally:
        conn.close()


def _save_message(msg: DisplayMessage, project_id: Optional[str] = None):
    """存一条消息

    project_id 可选 — 传入则存到 chat_messages 表, 让前端历史回放时能知道
    这条消息是哪个 project 产生的 (切换 project 后查历史能正确显示)
    """
    conn = get_connection()
    try:
        refs_json = (
            json.dumps([r.model_dump() for r in msg.references])
            if msg.references else None
        )
        conn.execute(
            """INSERT OR REPLACE INTO chat_messages
            (id, conversation_id, role, content, references_json, project_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg.id, msg.conversationId, msg.role, msg.content, refs_json, project_id, msg.timestamp),
        )
        conn.commit()
    finally:
        conn.close()


# ============ POST /api/chat (SSE) ============

@router.post("")
async def chat(
    body: ChatRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """SSE 流式 chat 端点

    Event types:
      - references: 一次（系统消息后），告知前端用了哪些 wiki 页
      - token: 每个 LLM token
      - reasoning: LLM reasoning token（DeepSeek-R1 等）
      - done: 流结束
      - error: 出错
    """
    # 1. 会话管理
    conv_id = body.conversationId or _gen_conv_id()
    if not body.conversationId:
        _save_conversation(
            Conversation(
                id=conv_id,
                title=body.query[:50],
                createdAt=int(time.time() * 1000),
                updatedAt=int(time.time() * 1000),
            ),
            project_id=body.projectId or "",
            user_id=current_user.get("id", 0),
        )

    # 2.5 先解析 rag_project_id (save_message 也用)
    rag_project_id = body.projectId or get_user_project_id(current_user["id"])

    # 2. 保存 user 消息
    user_msg = DisplayMessage(
        id=_gen_msg_id(),
        role="user",
        content=body.query,
        timestamp=int(time.time() * 1000),
        conversationId=conv_id,
    )
    _save_message(user_msg, project_id=rag_project_id)

    # 3. RAG 组装 system prompt
    try:
        system_prompt, references = await build_rag_context(
            project_id=rag_project_id,
            query=body.query,
            llm_cfg=body.llmConfig,
        )
    except Exception as e:
        logger.exception("RAG build failed")
        raise HTTPException(status_code=500, detail=f"RAG build failed: {e}")

    # 4. 组装 LLM messages（system + 历史 + user）
    history = body.history[-body.maxHistoryMessages:]
    llm_messages = (
        [{"role": "system", "content": system_prompt}]
        + [{"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant")]
        + [{"role": "user", "content": body.query}]
    )

    # 5. SSE 流式生成
    async def event_stream() -> AsyncIterator[str]:
        accumulated = ""

        def sse(payload: dict) -> str:
            """统一给所有 SSE 事件附 project_id, 前端能确认当前是哪个 project 在回答"""
            payload.setdefault("projectId", rag_project_id)
            return f"data: {json.dumps(payload)}\n\n"

        try:
            # 5a. 先发 references（让前端能提前显示引用的 wiki 页）
            yield sse({
                "type": "references",
                "references": [r.model_dump() for r in references],
                "conversationId": conv_id,
            })
            # 5b. 流式调 LLM
            async for event_type, content in stream_chat(body.llmConfig, llm_messages):
                if await request.is_disconnected():
                    logger.info("client disconnected, aborting")
                    break
                if event_type == "token":
                    accumulated += content
                    yield sse({"type": "token", "content": content})
                elif event_type == "reasoning":
                    yield sse({"type": "reasoning", "content": content})
                elif event_type == "error":
                    yield sse({"type": "error", "error": content})
                    return
                elif event_type == "done":
                    # 保存 assistant 消息
                    if accumulated:
                        assistant_msg = DisplayMessage(
                            id=_gen_msg_id(),
                            role="assistant",
                            content=accumulated,
                            timestamp=int(time.time() * 1000),
                            conversationId=conv_id,
                            references=references,
                        )
                        _save_message(assistant_msg, project_id=rag_project_id)
                    yield sse({"type": "done", "conversationId": conv_id})
                    return
        except Exception as e:
            logger.exception("stream error")
            yield sse({"type": "error", "error": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 不缓冲
        },
    )


# ============ GET /api/chat/conversations ============

@router.get("/conversations")
def list_conversations(
    project_id: str = None,
    current_user: dict = Depends(get_current_user),
):
    """列当前用户的会话"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, title, created_at, updated_at
            FROM chat_conversations
            WHERE user_id = ? AND (project_id = ? OR (? IS NULL))
            ORDER BY updated_at DESC
            LIMIT 200""",
            (current_user.get("id", 0), project_id, project_id),
        ).fetchall()
        return {
            "conversations": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "createdAt": r["created_at"],
                    "updatedAt": r["updated_at"],
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


@router.get("/conversations/{conv_id}/messages")
def get_messages(
    conv_id: str,
    current_user: dict = Depends(get_current_user),
):
    """取会话所有消息"""
    conn = get_connection()
    try:
        # 鉴权
        conv = conn.execute(
            "SELECT user_id FROM chat_conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if not conv or conv["user_id"] != current_user.get("id", 0):
            raise HTTPException(404, "Conversation not found")
        rows = conn.execute(
            """SELECT id, role, content, references_json, project_id, timestamp
            FROM chat_messages WHERE conversation_id = ?
            ORDER BY timestamp ASC""",
            (conv_id,),
        ).fetchall()
        msgs = []
        for r in rows:
            refs = json.loads(r["references_json"]) if r["references_json"] else None
            msgs.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "conversationId": conv_id,
                "references": refs,
                "projectId": r["project_id"],
            })
        return {"messages": msgs}
    finally:
        conn.close()


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    current_user: dict = Depends(get_current_user),
):
    """删除会话 + 消息"""
    conn = get_connection()
    try:
        conv = conn.execute(
            "SELECT user_id FROM chat_conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if not conv or conv["user_id"] != current_user.get("id", 0):
            raise HTTPException(404, "Conversation not found")
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
