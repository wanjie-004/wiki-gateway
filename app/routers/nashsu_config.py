"""Read nashsu desktop app config (llmConfig + apiConfig) — 暴露给前端

为什么不让前端直读文件？
- Tauri app 配置文件在用户家目录（~/.local/share/com.llmwiki.app/）
- 浏览器 fetch file:// 被安全策略拒绝
- 后端读文件 + JSON 解析 + 过滤敏感字段更安全

暴露的字段：
- provider / model / customEndpoint / apiKey
- maxContextSize / apiMode
- 不暴露：token（API token）、其他私钥
"""
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config"])

# nashsu 配置文件常见位置（按优先级）
NASHSU_APP_STATE_PATHS = [
    Path.home() / ".local" / "share" / "com.llmwiki.app" / "app-state.json",
    Path.home() / ".config" / "com.llmwiki.app" / "app-state.json",
    Path.home() / "Library" / "Application Support" / "com.llmwiki.app" / "app-state.json",
    Path("/mnt/c/Users") / Path.home().name / "AppData" / "Roaming" / "com.llmwiki.app" / "app-state.json",
]


def _find_app_state() -> Optional[Path]:
    """找 nashsu app-state.json 路径"""
    for p in NASHSU_APP_STATE_PATHS:
        if p.exists():
            return p
    return None


@router.get("/nashsu-llm")
def get_nashsu_llm_defaults(current_user: dict = Depends(get_current_user)):
    """从 nashsu app-state.json 提取 llmConfig，作为 wiki-gateway chat 的默认值

    返回字段（不全量透传，过滤敏感字段）：
    - provider, model, customEndpoint, apiKey, apiMode, maxContextSize
    - source: 配置文件路径 + activePresetId
    - 注意：每次返回一致结构（找不到时返回 provider=null），让前端能 prefill
    """
    p = _find_app_state()
    if not p:
        return {
            "found": False,
            "source_path": None,
            "provider": None,
            "model": None,
            "customEndpoint": None,
            "apiKey": None,
            "apiMode": None,
            "maxContextSize": 32000,
            "maxResponseTokens": 1024,
            "activePresetId": None,
        }
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        logger.warning("parse app-state.json failed: %s", e)
        raise HTTPException(500, f"parse app-state.json failed: {e}")
    cfg = data.get("llmConfig", {}) or {}
    return {
        "found": True,
        "source_path": str(p),
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "customEndpoint": cfg.get("customEndpoint"),
        "apiKey": cfg.get("apiKey"),
        "apiMode": cfg.get("apiMode"),
        "maxContextSize": cfg.get("maxContextSize", 32000),
        # maxResponseTokens nashsu 没有，用合理默认
        "maxResponseTokens": 1024,
        "activePresetId": data.get("activePresetId"),
    }
