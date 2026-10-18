"""Gated request optimizations: caching, warmers, and learned compression.

Every subsystem is opt-in and independently tri-state (``off | shadow |
wired``). This package never selects a model and never changes execution
authority; it only decides whether an already-selected request may be served
from a cache, compressed, or warmed.

Import submodules directly (for example
``machina.core.optimization.semantic_cache``); the package root stays
dependency-light so accounting/provider modules can import the seams without
pulling the provider package at module load.
"""
