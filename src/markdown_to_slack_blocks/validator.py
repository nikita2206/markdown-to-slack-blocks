"""Validate mention maps against Slack ID formats."""

from __future__ import annotations

import re
from typing import Callable, Mapping

_USER_ID = re.compile(r"^[UW][A-Z0-9]+$")
_CHANNEL_ID = re.compile(r"^C[A-Z0-9]+$")
_GROUP_ID = re.compile(r"^S[A-Z0-9]+$")
_TEAM_ID = re.compile(r"^T[A-Z0-9]+$")


def _mention_map(options: Mapping | None, *keys: str) -> Mapping[str, str] | None:
    if not options:
        return None
    mentions = options.get("mentions")
    if not isinstance(mentions, Mapping):
        return None
    for key in keys:
        value = mentions.get(key)
        if value:
            return value
    return None


def validate_options(options: Mapping | None = None) -> None:
    """Validate username → Slack ID maps used by ``markdown_to_blocks``."""
    if not options or not options.get("mentions"):
        return

    users = _mention_map(options, "users")
    channels = _mention_map(options, "channels")
    user_groups = _mention_map(options, "userGroups", "user_groups")
    teams = _mention_map(options, "teams")

    if users:
        _validate_id_values(
            users,
            _USER_ID,
            lambda name, id_: (
                f"Invalid User ID for '{name}': '{id_}'. "
                "Must start with U or W and contain only alphanumeric characters."
            ),
        )
    if channels:
        _validate_id_values(
            channels,
            _CHANNEL_ID,
            lambda name, id_: (
                f"Invalid Channel ID for '{name}': '{id_}'. "
                "Must start with C and contain only alphanumeric characters."
            ),
        )
    if user_groups:
        _validate_id_values(
            user_groups,
            _GROUP_ID,
            lambda name, id_: (
                f"Invalid User Group ID for '{name}': '{id_}'. "
                "Must start with S and contain only alphanumeric characters."
            ),
        )
    if teams:
        _validate_id_values(
            teams,
            _TEAM_ID,
            lambda name, id_: (
                f"Invalid Team ID for '{name}': '{id_}'. "
                "Must start with T and contain only alphanumeric characters."
            ),
        )


def validate_blocks_to_markdown_options(options: Mapping | None = None) -> None:
    """Validate Slack ID → name maps used by ``blocks_to_markdown``."""
    if not options or not options.get("mentions"):
        return

    users = _mention_map(options, "users")
    channels = _mention_map(options, "channels")
    user_groups = _mention_map(options, "userGroups", "user_groups")
    teams = _mention_map(options, "teams")

    if users:
        _validate_id_keys(
            users,
            _USER_ID,
            lambda id_: (
                f"Invalid User ID key '{id_}'. "
                "Must start with U or W and contain only alphanumeric characters."
            ),
        )
    if channels:
        _validate_id_keys(
            channels,
            _CHANNEL_ID,
            lambda id_: (
                f"Invalid Channel ID key '{id_}'. "
                "Must start with C and contain only alphanumeric characters."
            ),
        )
    if user_groups:
        _validate_id_keys(
            user_groups,
            _GROUP_ID,
            lambda id_: (
                f"Invalid User Group ID key '{id_}'. "
                "Must start with S and contain only alphanumeric characters."
            ),
        )
    if teams:
        _validate_id_keys(
            teams,
            _TEAM_ID,
            lambda id_: (
                f"Invalid Team ID key '{id_}'. "
                "Must start with T and contain only alphanumeric characters."
            ),
        )


def _validate_id_values(
    entries: Mapping[str, str],
    pattern: re.Pattern[str],
    build_message: Callable[[str, str], str],
) -> None:
    for name, id_ in entries.items():
        if not pattern.fullmatch(id_):
            raise ValueError(build_message(name, id_))


def _validate_id_keys(
    entries: Mapping[str, str],
    pattern: re.Pattern[str],
    build_message: Callable[[str], str],
) -> None:
    for id_ in entries:
        if not pattern.fullmatch(id_):
            raise ValueError(build_message(id_))
