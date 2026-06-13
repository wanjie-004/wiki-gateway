"""nashsu 19828 API 客户端（httpx 异步）"""
import httpx
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
