"""Per-user project state (SQLite 持久化 + in-memory cache)

启动时 db.py init_db() 后调 load_all() 从 user_project_state 表加载所有用户状态
切换项目时 set() 同时更新内存和 DB
"""
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from .. import db as appdb
from ..config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict[int, str] = {}


def _conn() -> sqlite3.Connection:
    return appdb.get_connection()


def load_all() -> int:
    """从 DB 加载所有 user → project_id 映射到内存 cache

    在 main.py lifespan 启动时调一次
    返回加载的条数
    """
    with _lock:
        try:
            conn = _conn()
            try:
                rows = conn.execute(
                    "SELECT user_id, project_id FROM user_project_state"
                ).fetchall()
                _state.clear()
                for r in rows:
                    _state[int(r["user_id"])] = r["project_id"]
                logger.info(f"[project_state] loaded {len(_state)} entries from DB")
                return len(_state)
            finally:
                conn.close()
        except Exception as e:
            logger.exception(f"[project_state] load_all failed: {e}")
            return 0


def set(user_id: int, project_id: str) -> None:
    """更新内存 + 写 DB (upsert)"""
    with _lock:
        _state[user_id] = project_id
    # DB write 不在 lock 内 (写库可能慢, 释放 lock 让其他用户切换不被阻塞)
    try:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO user_project_state (user_id, project_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    updated_at = excluded.updated_at
                """,
                (user_id, project_id, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.exception(f"[project_state] DB write failed (in-memory updated): {e}")


def get(user_id: int) -> Optional[str]:
    with _lock:
        return _state.get(user_id)


def clear(user_id: int) -> None:
    with _lock:
        _state.pop(user_id, None)
    try:
        conn = _conn()
        try:
            conn.execute("DELETE FROM user_project_state WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.exception(f"[project_state] clear failed: {e}")
