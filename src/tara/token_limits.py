"""Shared exact token accounting for managed web inputs."""

from __future__ import annotations

import tiktoken


class TokenLimitExceeded(ValueError):
    """Raised when accepted web input exceeds its contractual limit."""


def count_tokens(text: str) -> int:
    """Count tokens with the preloaded production BPE vocabulary."""
    return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))


def require_token_limit(text: str, maximum: int) -> None:
    if count_tokens(text) > maximum:
        raise TokenLimitExceeded("input exceeds its token limit")
