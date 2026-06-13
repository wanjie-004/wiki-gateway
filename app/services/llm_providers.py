"""LLM Provider 协议适配

从 nashsu llm-providers.ts 翻译，保留：
- 9 个 provider 接口
- 每个 provider 的 url/headers/buildBody/parseStream
- 但仅实现 3 个：openai / anthropic / custom

实现原则：
- 完全对标 nashsu body 格式（sending/receiving 完全一致）
- 支持 SSE 流式
- 支持 reasoning 字段（DeepSeek-R1 / Qwen）
- 不做：CLI subprocess、vision input、reasoning 复杂计算
"""
from typing import List, Dict, Any, Optional, Tuple
import json
import re

from ..schemas.chat import LlmConfig, MessageImage


# ============ Body Building ============

def _strip_images(messages: List[Dict]) -> List[Dict]:
    """图片附件 → 拆 text + image 块（OpenAI / Anthropic 多模态格式）"""
    out = []
    for m in messages:
        if isinstance(m.get("content"), str):
            out.append(m)
        else:
            out.append(m)  # 暂透传
    return out


def build_openai_body(cfg: LlmConfig, messages: List[Dict], overrides: Optional[Dict] = None) -> Dict:
    """OpenAI 兼容协议 body（也用于 custom / ollama）

    对标 nashsu buildOpenAiCompatibleBody
    """
    overrides = overrides or {}
    body = {
        "model": cfg.model,
        "messages": _strip_images(messages),
        "stream": True,
        "max_tokens": cfg.maxResponseTokens,
    }
    # 透传 sampling 参数
    for k in ("temperature", "top_p", "top_k", "stop"):
        if k in overrides:
            body[k] = overrides[k]
    return body


def build_anthropic_body(cfg: LlmConfig, messages: List[Dict], overrides: Optional[Dict] = None) -> Dict:
    """Anthropic Messages API body

    对标 nashsu buildAnthropicBodyWithReasoning
    关键差异：system 单独字段；max_tokens 必填
    """
    overrides = overrides or {}
    system_msgs = [m["content"] for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    body = {
        "model": cfg.model,
        "messages": non_system,
        "max_tokens": cfg.maxResponseTokens,
        "stream": True,
    }
    if system_msgs:
        body["system"] = "\n\n".join(system_msgs)
    for k in ("temperature", "top_p", "top_k", "stop"):
        if k in overrides:
            body[k] = overrides[k]
    return body


# ============ Stream Parsing ============

def parse_openai_line(line: str) -> Optional[str]:
    """OpenAI 兼容 SSE 解析

    格式：data: {"choices":[{"delta":{"content":"xxx"}}]}
    结束：data: [DONE]
    """
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    return delta.get("content") or None


def parse_anthropic_line(line: str) -> Optional[str]:
    """Anthropic SSE 解析

    格式：
    event: message_start
    data: {"type":"message_start",...}

    event: content_block_delta
    data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"xxx"}}
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if data.get("type") != "content_block_delta":
        return None
    delta = data.get("delta") or {}
    if delta.get("type") != "text_delta":
        return None
    return delta.get("text") or None


# ============ URL / Headers ============

def build_anthropic_url(base: str) -> str:
    return base.rstrip("/") + "/v1/messages"


def build_anthropic_headers(api_key: str, url: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",  # 浏览器直连时需要
    }


# ============ Provider Dispatch ============

class ProviderConfig:
    def __init__(self, url, headers, build_body, parse_stream):
        self.url = url
        self.headers = headers
        self.build_body = build_body
        self.parse_stream = parse_stream


def get_provider_config(cfg: LlmConfig) -> ProviderConfig:
    if cfg.provider == "openai":
        bearer = "Bearer " + cfg.apiKey
        return ProviderConfig(
            url="https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": bearer,
            },
            build_body=lambda msgs, ov: build_openai_body(cfg, msgs, ov),
            parse_stream=parse_openai_line,
        )
    if cfg.provider == "anthropic":
        url = build_anthropic_url("https://api.anthropic.com")
        return ProviderConfig(
            url=url,
            headers=build_anthropic_headers(cfg.apiKey, url),
            build_body=lambda msgs, ov: build_anthropic_body(cfg, msgs, ov),
            parse_stream=parse_anthropic_line,
        )
    if cfg.provider == "custom":
        if not cfg.customEndpoint:
            raise ValueError("custom provider requires customEndpoint")
        # 自定义端点：按 apiMode 决定协议
        # nashsu 配的就是 custom + anthropic_messages → 走 Anthropic
        api_mode = cfg.apiMode or "openai_chat"  # 默认兼容 openai
        base = cfg.customEndpoint.rstrip("/")
        if api_mode == "anthropic_messages":
            url = build_anthropic_url(base)
            return ProviderConfig(
                url=url,
                headers=build_anthropic_headers(cfg.apiKey, url),
                build_body=lambda msgs, ov: build_anthropic_body(cfg, msgs, ov),
                parse_stream=parse_anthropic_line,
            )
        # 默认 openai 兼容（vLLM / LocalAI / LM Studio / OpenRouter）
        if not base.endswith("/chat/completions"):
            if base.endswith("/v1"):
                base = base[:-3]
            url = base + "/v1/chat/completions"
        else:
            url = base
        bearer = "Bearer " + (cfg.apiKey or "")
        return ProviderConfig(
            url=url,
            headers={
                "Content-Type": "application/json",
                "Authorization": bearer,
            },
            build_body=lambda msgs, ov: build_openai_body(cfg, msgs, ov),
            parse_stream=parse_openai_line,
        )
    raise NotImplementedError(f"Provider '{cfg.provider}' not yet implemented in wiki-gateway")