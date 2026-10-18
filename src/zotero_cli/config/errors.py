"""Typed configuration errors shared by loaders, commands, and the CLI boundary."""

from __future__ import annotations


class ConfigError(Exception):
    """Configuration is missing, unreadable, malformed, or has invalid values.

    The CLI maps this to a typed `configuration_error` (exit 3); message text must
    stay actionable and must never include credential values.
    """

    code = "configuration_error"
    retryable = False
