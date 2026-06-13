"""Chat 模块的 Pydantic schema

设计：完全对标 nashsu 前端 chat-store + llm-providers 的数据形状
这样未来可以无缝迁移 nashsu 的 .llm-wiki/chats/*.json
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Union
from datetime import datetime


# ============ LLM Provider Config ============

class LlmConfig(BaseModel):
    """LLM provider 配置 — 对标 nashsu LlmConfig

    provider: openai | anthropic | custom
    customEndpoint: 自建 vLLM / LocalAI / LM Studio 端点
    apiMode: custom 端点协议 — openai_chat (默认) | anthropic_messages
    """
    provider: Literal["openai", "anthropic", "custom"]
    apiKey: str
    model: str
    customEndpoint: Optional[str] = None
    apiMode: Optional[str] = None  # custom 用：openai_chat | anthropic_messages
    maxContextSize: int = 32000
    maxResponseTokens: int = 1024


# ============ Chat Messages ============

class MessageImage(BaseModel):
    """用户消息图片附件（vision input）— 对标 nashsu MessageImage"""
    mediaType: str
    dataBase64: str


class MessageReference(BaseModel):
    """Assistant 消息引用的 wiki 页 / 外部源 — 对标 nashsu MessageReference"""
    title: str
    path: str
    kind: Literal["wiki", "external"] = "wiki"
    source: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None


class DisplayMessage(BaseModel):
    """一条消息（user/assistant/system）— 对标 nashsu DisplayMessage"""
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: int
    conversationId: str
    references: Optional[List[MessageReference]] = None
    images: Optional[List[MessageImage]] = None


class Conversation(BaseModel):
    id: str
    title: str
    createdAt: int
    updatedAt: int


# ============ Request / Response ============

class ChatRequest(BaseModel):
    """前端发起的 chat 请求"""
    query: str = Field(min_length=1)
    conversationId: Optional[str] = None
    history: List[DisplayMessage] = Field(default_factory=list)
    llmConfig: LlmConfig
    projectId: Optional[str] = None  # None = current project
    useWebSearch: bool = False        # 暂不实现，留接口
    useAnyTxtSearch: bool = False     # 暂不实现，留接口
    maxHistoryMessages: int = Field(default=10, ge=1, le=50)


class ChatSseEvent(BaseModel):
    """SSE 事件格式：data: <json>\\n\\n"""
    type: Literal["token", "reasoning", "done", "error", "references"]
    content: Optional[str] = None
    references: Optional[List[MessageReference]] = None
    error: Optional[str] = None


# ============ 持久化（DB 内部）============

class ChatHistoryResponse(BaseModel):
    conversations: List[Conversation]
    messages: List[DisplayMessage]
