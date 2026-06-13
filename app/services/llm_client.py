"""streamChat — httpx SSE 流式调 LLM

对标 nashsu llm-client.ts:streamChat
- 同 provider dispatch
- 同 SSE 解析
- 同 reasoning 分离
- 同 timeout / abort 语义

差异：
- 用 httpx.AsyncClient（python 等价 fetch）
- 返回 AsyncIterator[Tuple[type, str]]  (type = "token" | "reasoning")
- 不做 vision / image input 翻译
"""
import asyncio
import json
import re
from typing import AsyncIterator, List, Dict, Optional, Tuple

import httpx

from ..schemas.chat import LlmConfig
from . import llm_providers


REASONING_RE = re.compile(r'"reasoning_content"\s*:\s*"((?:[^"\\]|\\.)*)"')


def extract_reasoning_from_line(line: str) -> List[str]:
    """从 SSE 行提取 reasoning 字段（DeepSeek-R1 / Kimi / Qwen）

    对标 nashsu extractReasoningTextFromLine
    """
    return REASONING_RE.findall(line)


async def stream_chat(
    cfg: LlmConfig,
    messages: List[Dict],
    signal: Optional[asyncio.Event] = None,
) -> AsyncIterator[Tuple[str, str]]:
    """流式调 LLM，yield (event_type, content) 元组

    event_type ∈ {"token", "reasoning", "error", "done"}

    signal: asyncio.Event — 外部设 set() 即取消请求
    """
    prov = llm_providers.get_provider_config(cfg)
    body = prov.build_body(messages, None)
    headers = prov.headers

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30 * 60, connect=10)) as client:
            try:
                req_ctx = client.stream("POST", prov.url, json=body, headers=headers)
                response = await req_ctx.__aenter__()
            except Exception as e:
                yield ("error", f"connect failed: {type(e).__name__}: {e}")
                return

            try:
                if response.status_code != 200:
                    err_text = await response.aread()
                    yield ("error", f"HTTP {response.status_code}: {err_text.decode('utf-8', errors='replace')[:500]}")
                    return

                line_buffer = ""
                # 用 IncrementalDecoder 避免 httpx chunk 边界切到 UTF-8 字符中间导致乱码
                decoder_factory = __import__("codecs").getincrementaldecoder("utf-8")
                chunk_decoder = decoder_factory(errors="strict")
                async for chunk in response.aiter_bytes():
                    if signal and signal.is_set():
                        yield ("done", "aborted")
                        return
                    # IncrementalDecoder 内部保留不完整字节, 跨 chunk 字符完整
                    text = chunk_decoder.decode(chunk)
                    line_buffer += text
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        for r in extract_reasoning_from_line(line):
                            yield ("reasoning", r)
                        token = prov.parse_stream(line)
                        if token:
                            yield ("token", token)
                # flush 末尾不完整字节 (如果还有)
                text = chunk_decoder.decode(b"", final=True)
                if text:
                    line_buffer += text
                if line_buffer.strip():
                    for r in extract_reasoning_from_line(line_buffer):
                        yield ("reasoning", r)
                    token = prov.parse_stream(line_buffer)
                    if token:
                        yield ("token", token)
                yield ("done", "ok")
            finally:
                # 显式释放 response / 流
                try:
                    await req_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
    except httpx.RequestError as e:
        yield ("error", f"Network error: {type(e).__name__}: {e}")
    except Exception as e:
        yield ("error", f"{type(e).__name__}: {e}")
