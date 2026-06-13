"""SQLite 用户表封装"""
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_connection():
    """获取 SQLite 连接（每次新建，简单可靠）"""
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库（启动时调用）"""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

            -- per-user 选中的 project (前端顶栏下拉框)
            -- 启动时 db.py 加载到 in-memory cache (projects_state.py),
            -- 切项目时 upsert 到这张表 (跨重启不丢)
            CREATE TABLE IF NOT EXISTS user_project_state (
                user_id INTEGER PRIMARY KEY,
                project_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        conn.commit()

        # 创建默认 admin 账号
        if not get_user_by_username(settings.admin_username):
            create_user(
                username=settings.admin_username,
                password=settings.admin_password,
                full_name="系统管理员",
                role="admin"
            )
            print(f"✅ 创建默认 admin 账号: {settings.admin_username}")
    finally:
        conn.close()


def create_user(username: str, password: str, full_name: Optional[str] = None, role: str = "user") -> int:
    """创建用户"""
    conn = get_connection()
    try:
        password_hash = pwd_context.hash(password)
        now = datetime.now().isoformat()
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, full_name, role, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    """按用户名查用户"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """按 ID 查用户"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_password(plain_password: str, password_hash: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, password_hash)


def update_last_login(user_id: int):
    """更新最后登录时间"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (datetime.now().isoformat(), user_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_users() -> list[dict]:
    """列出所有用户（admin 专用）"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, full_name, role, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
