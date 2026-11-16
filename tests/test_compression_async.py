"""The async-only compressor keeps the truncation fallback contract."""

from __future__ import annotations

import uuid

import pytest

from machina.core.compression import CompressionConfig, ContextCompressor
from machina.core.models import ContextChunk


def _chunk(session_id: uuid.UUID, content: str, *, chunk_type: str = "user_message"):
    return ContextChunk(
        id=uuid.uuid4(),
        session_id=session_id,
        agent_id="harness",
        chunk_type=chunk_type,
        payload={"content": content},
        token_count=20,
    )


@pytest.mark.asyncio
async def test_acompress_truncates_without_an_llm_provider() -> None:
    session_id = uuid.uuid4()
    chunks = [_chunk(session_id, f"message {index}") for index in range(3)]
    compressor = ContextCompressor(CompressionConfig(preserve_recent=1, target_ratio=0.5))

    result = await compressor.acompress(
        chunks,
        session_id,
        "harness",
        llm_provider=None,
    )

    assert result.method == "truncate"
    assert result.original_count == 2
    assert result.compressed_chunks
    assert 0.0 <= result.compression_ratio <= 1.0
