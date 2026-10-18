"""Learned compression: protected spans, quality guards, optional backend."""

from __future__ import annotations

from machina.core.optimization.compression.learned import (
    CompressionResult,
    build_compressor,
    compress_messages,
    compressor_revision,
    maybe_compress_request,
)
from machina.core.optimization.compression.quality_guard import (
    anchor_retention,
    evaluate_compression,
)
from machina.core.optimization.compression.span_protector import compressible_spans

__all__ = [
    "CompressionResult",
    "anchor_retention",
    "build_compressor",
    "compress_messages",
    "compressible_spans",
    "compressor_revision",
    "evaluate_compression",
    "maybe_compress_request",
]
