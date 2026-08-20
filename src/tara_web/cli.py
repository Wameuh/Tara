"""Compatibility entry point for local/private operational commands."""

from .operator import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
