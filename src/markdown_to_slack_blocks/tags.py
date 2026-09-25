"""XML tag handlers for Markdown that contains custom elements.

Agent output often wraps a region in an element such as ``<sources>`` or
``<detailed>``. Those tags are parsed with Python's expat XML parser
(``xml.parsers.expat``). The handler receives the decoded attributes and the
inner Markdown, unchanged, and usually returns a Slack ``container`` block.

The text inside an element is Markdown, not XML, so it is not fed to the
parser. A comparison such as ``a < b`` or a raw ``&`` in the body stays as
it was written. Attribute values do go through the XML parser, which means
they must be quoted and entities such as ``&`` are decoded.
"""

from __future__ import annotations

import json
import re
import xml.parsers.expat as expat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

Block = dict[str, Any]
XmlTagHandler = Callable[["XmlTagContext"], Block | list[Block] | None]

_REGISTRY: dict[str, XmlTagHandler] = {}

_FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})")
_PLACEHOLDER = "\ue000mdslack:{}\ue000"
_PLACEHOLDER_TEXT = re.compile(r"^\ue000mdslack:\d+\ue000$")
_MAX_TAG_LENGTH = 65536


@dataclass(frozen=True)
class XmlTagContext:
    """One custom XML element found in the Markdown source.

    ``name`` and ``attrs`` come from the XML parser. ``body`` is the original
    Markdown between the start and end tags. ``convert`` parses that body
    with the same options as the outer call, including nested XML tag handlers.
    """

    name: str
    attrs: Mapping[str, str]
    body: str
    options: Mapping[str, Any]
    convert: Callable[[str], list[Block]]


def register_xml_tag_handler(name: str, handler: XmlTagHandler | None) -> None:
    """Register a process-wide handler, or remove it when ``handler`` is None.

    Per-call ``xml_tag_handlers`` override this registry. Names are
    case-sensitive, matching XML.
    """
    if handler is None:
        _REGISTRY.pop(name, None)
    else:
        _REGISTRY[name] = handler


def clear_xml_tag_handlers() -> None:
    """Remove every process-wide XML tag handler."""
    _REGISTRY.clear()


SLACK_BLOCKS_TAG = "slack-blocks"


def slack_blocks_handler(tag: XmlTagContext) -> Block | list[Block]:
    """Turn ``<slack-blocks>`` into the blocks described by its JSON body.

    Register it as the handler for ``slack-blocks``. The body is either a
    JSON list of Block Kit blocks, one block object, or a message object
    ``{"text": "...", "blocks": [...]}``. ``text`` is not posted to Slack;
    it is the Markdown fallback for a web UI (see ``slack_blocks_to_text``).
    A ```json fence around the JSON is ignored. If the body is not that
    JSON, it is converted as ordinary Markdown instead of being dropped.
    """
    parsed = _parse_slack_blocks_body(tag.body)
    if parsed is None:
        return tag.convert(tag.body.strip())
    if parsed["blocks"]:
        return parsed["blocks"]
    if parsed["text"]:
        return tag.convert(parsed["text"])
    return []


def slack_blocks_to_text(markdown: str) -> str:
    """Replace ``<slack-blocks>`` so a Markdown web UI does not show raw JSON.

    A message object's ``text`` field is inserted as Markdown. Without one,
    the blocks are rendered with ``blocks_to_markdown``. A body that is not
    JSON is kept as Markdown, tags removed. Fenced code is left unchanged.
    """
    if not markdown:
        return markdown
    prepared, replacements = extract_xml_tags(markdown, {SLACK_BLOCKS_TAG})
    if not replacements:
        return markdown
    for placeholder, element in replacements.items():
        prepared = prepared.replace(placeholder, _slack_blocks_fallback_markdown(element["body"]))
    return prepared


def _parse_slack_blocks_body(body: str) -> dict[str, Any] | None:
    raw = _strip_wrapping_fence(body)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        blocks = _as_blocks(payload)
        if blocks is None:
            return None
        return {"blocks": blocks, "text": None}
    if not isinstance(payload, dict):
        return None
    if "blocks" in payload:
        if not isinstance(payload["blocks"], list):
            return None
        blocks = _as_blocks(payload["blocks"])
        if blocks is None:
            return None
        text = payload.get("text")
        if text is not None and not isinstance(text, str):
            return None
        return {"blocks": blocks, "text": text}
    if isinstance(payload.get("type"), str):
        return {"blocks": [dict(payload)], "text": None}
    if isinstance(payload.get("text"), str):
        return {"blocks": [], "text": payload["text"]}
    return None


def _as_blocks(items: list[Any]) -> list[Block] | None:
    blocks: list[Block] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            return None
        blocks.append(dict(item))
    return blocks


def _slack_blocks_fallback_markdown(body: str) -> str:
    parsed = _parse_slack_blocks_body(body)
    if parsed is None:
        return body.strip()
    if parsed["text"]:
        return parsed["text"].strip()
    if parsed["blocks"]:
        from .splitter import blocks_to_markdown

        return blocks_to_markdown(parsed["blocks"]).strip()
    return ""


def _strip_wrapping_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if (
        len(lines) >= 2
        and lines[0].startswith("```")
        and set(lines[-1].strip()) <= {"`"}
        and len(lines[-1].strip()) >= 3
    ):
        return "\n".join(lines[1:-1]).strip()
    return text.strip()


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
    block: Block = {"type": "container", "child_blocks": _container_children(list(child_blocks))}
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


def _container_children(children: list[Block]) -> list[Block]:
    from .parser import coerce_container_children

    return coerce_container_children(children)


def resolve_xml_tag_handlers(options: Mapping[str, Any] | None) -> dict[str, XmlTagHandler]:
    resolved = dict(_REGISTRY)
    if not options:
        return resolved
    custom = options.get("xml_tag_handlers", options.get("xmlTagHandlers"))
    if not isinstance(custom, Mapping):
        return resolved
    for name, handler in custom.items():
        key = str(name)
        if handler is None:
            resolved.pop(key, None)
        else:
            resolved[key] = handler
    return resolved


def extract_xml_tags(
    markdown: str,
    names: set[str],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Replace outermost registered elements with placeholders.

    Elements inside fenced code blocks are left alone. For a registered name,
    a start tag with no close runs until the next registered start tag or the
    end of the input, and a close tag that was never opened is dropped.
    Unregistered tags stay in the Markdown.
    """
    if not names or not markdown:
        return markdown, {}

    result: list[str] = []
    replacements: dict[str, dict[str, Any]] = {}
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
                result.append(markdown[index:next_index])
                index = next_index
                continue

        if not in_fence and _could_be_xml_tag(markdown, index):
            element = _parse_element(markdown, index, names)
            if element is not None:
                placeholder = _PLACEHOLDER.format(counter)
                counter += 1
                replacements[placeholder] = element
                result.append(f"\n\n{placeholder}\n\n")
                index = element["end"]
                continue
            close_length = _registered_close_length(markdown[index:], names)
            if close_length:
                index += close_length
                continue

        result.append(markdown[index])
        index += 1

    return "".join(result), replacements


def _parse_element(
    markdown: str,
    index: int,
    names: set[str],
) -> dict[str, Any] | None:
    """Parse one registered element, or return None if this is not one.

    A matching close tag wins. Otherwise the element runs to the next
    registered start tag, or to the end of the input. Ordinary ``<``
    characters such as ``2 < 5``, and unregistered tags, return None.
    """
    start = _parse_start_tag(markdown[index : index + _MAX_TAG_LENGTH])
    if start is None or start["name"] not in names:
        return None
    tag_end = index + start["length"]
    if start["self_closing"]:
        return {
            "name": start["name"],
            "attrs": start["attrs"],
            "body": "",
            "raw": markdown[index:tag_end],
            "end": tag_end,
        }
    found = _find_close(markdown, tag_end, start["name"])
    if found is not None:
        close_at, close_length = found
        return {
            "name": start["name"],
            "attrs": start["attrs"],
            "body": markdown[tag_end:close_at],
            "raw": markdown[index : close_at + close_length],
            "end": close_at + close_length,
        }
    boundary = _find_next_registered_start(markdown, tag_end, names)
    end = len(markdown) if boundary is None else boundary
    return {
        "name": start["name"],
        "attrs": start["attrs"],
        "body": markdown[tag_end:end],
        "raw": markdown[index:end],
        "end": end,
    }


def _find_close(markdown: str, body_start: int, name: str) -> tuple[int, int] | None:
    """Find the end tag that closes ``name``, skipping fences and nesting."""
    depth = 1
    index = body_start
    length = len(markdown)
    in_fence = False
    fence_char = ""
    fence_len = 0
    while index < length:
        if _at_line_start(markdown, index):
            toggled, next_index = _consume_fence_line(
                markdown, index, in_fence, fence_char, fence_len
            )
            if toggled is not None:
                in_fence, fence_char, fence_len = toggled
                index = next_index
                continue
        if not in_fence and _could_be_xml_tag(markdown, index):
            start = _parse_start_tag(markdown[index : index + _MAX_TAG_LENGTH])
            if start is not None and start["name"] == name:
                if not start["self_closing"]:
                    depth += 1
                index += start["length"]
                continue
            if markdown.startswith("</", index):
                end_length = _parse_end_tag(markdown[index : index + _MAX_TAG_LENGTH], name)
                if end_length:
                    depth -= 1
                    if depth == 0:
                        return index, end_length
                    index += end_length
                    continue
        index += 1
    return None


def _find_next_registered_start(markdown: str, start: int, names: set[str]) -> int | None:
    """Index of the next registered start tag, skipping fenced code."""
    index = start
    length = len(markdown)
    in_fence = False
    fence_char = ""
    fence_len = 0
    while index < length:
        if _at_line_start(markdown, index):
            toggled, next_index = _consume_fence_line(
                markdown, index, in_fence, fence_char, fence_len
            )
            if toggled is not None:
                in_fence, fence_char, fence_len = toggled
                index = next_index
                continue
        if not in_fence and _could_be_xml_tag(markdown, index) and not markdown.startswith("</", index):
            parsed = _parse_start_tag(markdown[index : index + _MAX_TAG_LENGTH])
            if parsed is not None and parsed["name"] in names:
                return index
        index += 1
    return None


def _registered_close_length(fragment: str, names: set[str]) -> int | None:
    if not fragment.startswith("</"):
        return None
    for name in names:
        length = _parse_end_tag(fragment, name)
        if length:
            return length
    return None


def apply_xml_tag_replacements(
    blocks: list[Block],
    replacements: dict[str, dict[str, Any]],
    handlers: dict[str, XmlTagHandler],
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
    handlers: dict[str, XmlTagHandler],
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
    handlers: dict[str, XmlTagHandler],
    options: Mapping[str, Any],
    convert: Callable[[str], list[Block]],
) -> list[Block]:
    handler = handlers.get(replacement["name"])
    if handler is None:
        return _convert_without(replacement["raw"], replacement["name"], options)
    context = XmlTagContext(
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
    """Reparse an element as ordinary Markdown, without this tag's handler."""
    from .parser import markdown_to_blocks

    fallback = dict(options)
    disabled = dict(resolve_xml_tag_handlers(options))
    disabled[name] = None
    fallback["xml_tag_handlers"] = disabled
    return markdown_to_blocks(source, fallback)


def _could_be_xml_tag(text: str, index: int) -> bool:
    if text[index] != "<" or index + 1 >= len(text):
        return False
    nxt = text[index + 1]
    if nxt == "/":
        return index + 2 < len(text) and (text[index + 2].isalpha() or text[index + 2] in "_:")
    return nxt.isalpha() or nxt in "_:"


def _parse_start_tag(fragment: str) -> dict[str, Any] | None:
    """Parse one XML start tag at the front of ``fragment`` with expat."""
    if not fragment.startswith("<") or fragment.startswith("</"):
        return None
    limit = min(len(fragment), _MAX_TAG_LENGTH)
    found: dict[str, Any] | None = None
    lo = 2
    hi = limit
    while lo <= hi:
        mid = (lo + hi) // 2
        probed = _probe_start(fragment[:mid])
        if probed is None:
            lo = mid + 1
        else:
            found = probed
            found["length"] = mid
            hi = mid - 1
    return found


def _probe_start(snippet: str) -> dict[str, Any] | None:
    parser = expat.ParserCreate()
    info: dict[str, Any] = {}

    def start(name: str, attrs: dict[str, str]) -> None:
        info["name"] = name
        info["attrs"] = {key: str(value) for key, value in attrs.items()}

    def end(_name: str) -> None:
        info["self_closing"] = True

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(snippet, False)
    except expat.ExpatError:
        if "name" not in info:
            return None
    if "name" not in info:
        return None
    info.setdefault("self_closing", False)
    return info


def _parse_end_tag(fragment: str, name: str) -> int | None:
    """Return the length of a closing ``</name>`` tag, or None."""
    if not fragment.startswith("</"):
        return None
    limit = min(len(fragment), _MAX_TAG_LENGTH)
    found: int | None = None
    lo = 3
    hi = limit
    while lo <= hi:
        mid = (lo + hi) // 2
        if _probe_end(name, fragment[:mid]):
            found = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return found


def _probe_end(name: str, snippet: str) -> bool:
    parser = expat.ParserCreate()
    closed = False

    def end(tag_name: str) -> None:
        nonlocal closed
        if tag_name == name:
            closed = True

    parser.EndElementHandler = end
    try:
        parser.Parse(f"<{name}>", False)
        parser.Parse(snippet, False)
    except expat.ExpatError:
        return closed
    return closed


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
