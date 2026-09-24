"""Custom XML tags in Markdown, turned into Slack blocks by user handlers.

Agent output often wraps a region in a tag such as ``<sources>`` or
``<detailed>``. Register a handler for that tag name and it receives the
inner Markdown, already convertible with the same options. A typical handler
returns a Slack ``container`` block.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

Block = dict[str, Any]
TagHandler = Callable[["TagContext"], Block | list[Block] | None]

_REGISTRY: dict[str, TagHandler] = {}

_OPEN_TAG = re.compile(
    r"<([A-Za-z][A-Za-z0-9_-]*)((?:\s+[^<>]*?)?)\s*(/?)>",
)
_CLOSE_TAG = re.compile(r"</([A-Za-z][A-Za-z0-9_-]*)\s*>")
_ATTR = re.compile(
    r"""([A-Za-z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?"""
)
_FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})")
_PLACEHOLDER = "\ue000mdslack:{}\ue000"
_PLACEHOLDER_TEXT = re.compile(r"^\ue000mdslack:\d+\ue000$")
_UNESCAPE = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
}


@dataclass(frozen=True)
class TagContext:
    """One custom tag found in the Markdown source.

    ``convert`` parses ``body`` with the same options as the outer call,
    including nested tag handlers.
    """

    name: str
    attrs: Mapping[str, str]
    body: str
    options: Mapping[str, Any]
    convert: Callable[[str], list[Block]]


def register_tag_handler(name: str, handler: TagHandler | None) -> None:
    """Register a process-wide handler, or remove it when ``handler`` is None.

    Per-call ``tag_handlers`` override this registry.
    """
    key = name.lower()
    if handler is None:
        _REGISTRY.pop(key, None)
    else:
        _REGISTRY[key] = handler


def clear_tag_handlers() -> None:
    """Remove every process-wide tag handler."""
    _REGISTRY.clear()


def container_block(
    title: str,
    child_blocks: list[Block],
    *,
    subtitle: str | None = None,
    collapsible: bool | None = None,
    default_collapsed: bool | None = None,
    width: str | None = None,
    block_id: str | None = None,
    icon: Mapping[str, Any] | None = None,
    rich_text_title: Mapping[str, Any] | None = None,
    has_header_divider: bool | None = None,
) -> Block:
    """Build a Slack ``container`` block.

    Slack requires ``child_blocks`` (at most 10) and either ``title``
    (plain text, at most 150 characters) or ``rich_text_title``.
    ``width`` is ``narrow``, ``standard``, ``wide``, or ``full``.
    """
    block: Block = {"type": "container", "child_blocks": list(child_blocks)}
    if rich_text_title is not None:
        block["rich_text_title"] = dict(rich_text_title)
    else:
        block["title"] = {"type": "plain_text", "text": title}
    if subtitle is not None:
        block["subtitle"] = {"type": "plain_text", "text": subtitle}
    if collapsible is not None:
        block["is_collapsible"] = collapsible
    if default_collapsed is not None:
        block["default_collapsed"] = default_collapsed
    if width is not None:
        block["width"] = width
    if block_id is not None:
        block["block_id"] = block_id
    if icon is not None:
        block["icon"] = dict(icon)
    if has_header_divider is not None:
        block["has_header_divider"] = has_header_divider
    return block


def resolve_tag_handlers(options: Mapping[str, Any] | None) -> dict[str, TagHandler]:
    resolved = dict(_REGISTRY)
    if not options:
        return resolved
    custom = options.get("tag_handlers", options.get("tagHandlers"))
    if not isinstance(custom, Mapping):
        return resolved
    for name, handler in custom.items():
        key = str(name).lower()
        if handler is None:
            resolved.pop(key, None)
        else:
            resolved[key] = handler
    return resolved


def extract_custom_tags(
    markdown: str,
    names: set[str],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Replace outermost registered tags with placeholders.

    Tags inside fenced code blocks are left alone. An unclosed tag is left
    in the source so the rest of the document still converts.
    """
    if not names or not markdown:
        return markdown, {}

    result: list[str] = []
    replacements: dict[str, dict[str, Any]] = {}
    stack: list[tuple[str, dict[str, str], int, int]] = []
    outer_start = 0
    index = 0
    length = len(markdown)
    in_fence = False
    fence_char = ""
    fence_len = 0
    counter = 0

    while index < length:
        if _at_line_start(markdown, index):
            toggled, next_index = _consume_fence_line(
                markdown, index, in_fence, fence_char, fence_len
            )
            if toggled is not None:
                in_fence, fence_char, fence_len = toggled
                if not stack:
                    result.append(markdown[index:next_index])
                index = next_index
                continue

        if not in_fence and markdown.startswith("<", index):
            opened = _OPEN_TAG.match(markdown, index)
            if opened and opened.group(1).lower() in names and not opened.group(3):
                name = opened.group(1).lower()
                if not stack:
                    outer_start = index
                stack.append((name, _parse_attrs(opened.group(2) or ""), index, opened.end()))
                index = opened.end()
                continue
            if opened and opened.group(3) and opened.group(1).lower() in names and not stack:
                placeholder = _PLACEHOLDER.format(counter)
                counter += 1
                replacements[placeholder] = {
                    "name": opened.group(1).lower(),
                    "attrs": _parse_attrs(opened.group(2) or ""),
                    "body": "",
                    "raw": opened.group(0),
                }
                result.append(f"\n\n{placeholder}\n\n")
                index = opened.end()
                continue
            closed = _CLOSE_TAG.match(markdown, index)
            if closed and stack and closed.group(1).lower() == stack[-1][0]:
                name, attrs, _raw_at, body_start = stack.pop()
                index = closed.end()
                if stack:
                    continue
                placeholder = _PLACEHOLDER.format(counter)
                counter += 1
                replacements[placeholder] = {
                    "name": name,
                    "attrs": attrs,
                    "body": markdown[body_start:closed.start()],
                    "raw": markdown[outer_start:index],
                }
                result.append(f"\n\n{placeholder}\n\n")
                continue

        if not stack:
            result.append(markdown[index])
        index += 1

    if stack:
        result.append(markdown[outer_start:])
    return "".join(result), replacements


def apply_tag_replacements(
    blocks: list[Block],
    replacements: dict[str, dict[str, Any]],
    handlers: dict[str, TagHandler],
    options: Mapping[str, Any],
    convert: Callable[[str], list[Block]],
) -> list[Block]:
    if not replacements:
        return blocks
    expanded: list[Block] = []
    for block in blocks:
        expanded.extend(_expand_block(block, replacements, handlers, options, convert))
    return expanded


def _expand_block(
    block: Block,
    replacements: dict[str, dict[str, Any]],
    handlers: dict[str, TagHandler],
    options: Mapping[str, Any],
    convert: Callable[[str], list[Block]],
) -> list[Block]:
    if block.get("type") == "section":
        found = _match_placeholder((block.get("text") or {}).get("text") or "")
        if found and found in replacements:
            return _invoke(replacements[found], handlers, options, convert)
        return [block]

    if block.get("type") != "rich_text":
        return [block]

    pieces: list[Block] = []
    pending: list[dict[str, Any]] = []
    replaced = False
    for element in block.get("elements") or []:
        found = _match_section_placeholder(element)
        if found and found in replacements:
            if pending:
                pieces.append({"type": "rich_text", "elements": pending})
                pending = []
            pieces.extend(_invoke(replacements[found], handlers, options, convert))
            replaced = True
        else:
            pending.append(element)
    if not replaced:
        return [block]
    if pending:
        pieces.append({"type": "rich_text", "elements": pending})
    return pieces


def _invoke(
    replacement: dict[str, Any],
    handlers: dict[str, TagHandler],
    options: Mapping[str, Any],
    convert: Callable[[str], list[Block]],
) -> list[Block]:
    handler = handlers.get(replacement["name"])
    if handler is None:
        return _convert_without(replacement["raw"], replacement["name"], options)
    context = TagContext(
        name=replacement["name"],
        attrs=replacement["attrs"],
        body=replacement["body"],
        options=options,
        convert=convert,
    )
    produced = handler(context)
    if produced is None:
        return _convert_without(replacement["raw"], replacement["name"], options)
    if isinstance(produced, Mapping):
        return [dict(produced)]
    return [dict(block) for block in produced]


def _convert_without(source: str, name: str, options: Mapping[str, Any]) -> list[Block]:
    """Reparse a tag as ordinary Markdown, without this tag's handler."""
    from .parser import markdown_to_blocks

    fallback = dict(options)
    disabled = dict(resolve_tag_handlers(options))
    disabled[name] = None
    fallback["tag_handlers"] = disabled
    return markdown_to_blocks(source, fallback)


def _match_placeholder(text: str) -> str | None:
    stripped = text.strip()
    if _PLACEHOLDER_TEXT.fullmatch(stripped):
        return stripped
    return None


def _match_section_placeholder(element: Mapping[str, Any]) -> str | None:
    if element.get("type") != "rich_text_section":
        return None
    children = element.get("elements") or []
    if len(children) != 1 or children[0].get("type") != "text":
        return None
    if children[0].get("style"):
        return None
    return _match_placeholder(children[0].get("text") or "")


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR.finditer(raw):
        key = match.group(1)
        if match.group(2) is not None:
            value = match.group(2)
        elif match.group(3) is not None:
            value = match.group(3)
        elif match.group(4) is not None:
            value = match.group(4)
        else:
            value = "true"
        attrs[key] = _unescape(value)
    return attrs


def _unescape(value: str) -> str:
    return re.sub(
        r"&(?:(amp|lt|gt|quot|apos)|#(\d+)|#x([0-9A-Fa-f]+));",
        _unescape_entity,
        value,
    )


def _unescape_entity(match: re.Match[str]) -> str:
    if match.group(1):
        return _UNESCAPE[match.group(1)]
    if match.group(2):
        return chr(int(match.group(2)))
    return chr(int(match.group(3), 16))


def _at_line_start(text: str, index: int) -> bool:
    return index == 0 or text[index - 1] == "\n"


def _consume_fence_line(
    text: str,
    index: int,
    in_fence: bool,
    fence_char: str,
    fence_len: int,
) -> tuple[tuple[bool, str, int] | None, int]:
    line_end = text.find("\n", index)
    line = text[index:] if line_end == -1 else text[index:line_end]
    next_index = len(text) if line_end == -1 else line_end + 1
    if not in_fence:
        opened = _FENCE_OPEN.match(line)
        if not opened:
            return None, index
        marker = opened.group(2)
        return (True, marker[0], len(marker)), next_index
    if re.match(rf"^( {{0,3}}){fence_char}{{{fence_len},}}\s*$", line):
        return (False, "", 0), next_index
    return None, index
