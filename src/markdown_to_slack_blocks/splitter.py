"""Split Block Kit payloads and render them back to Markdown or plain text."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .parser import (
    SLACK_HEADER_MAX,
    coerce_container_children,
    heading_text_to_sections,
)
from .validator import validate_blocks_to_markdown_options

Block = dict[str, Any]

DEFAULT_MAX_BLOCKS = 40
DEFAULT_MAX_CHARACTERS = 12000
DEFAULT_MAX_TEXT_SECTION_CHARACTERS = 3000

_BOUNDARY = re.compile(r"""[\s.,!?;:()\[\]{}"'<>/-]""")
_WS = re.compile(r"\s")


def js_stringify(value: Any) -> str:
    """JSON.stringify equivalent used for Slack payload size checks."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def split_blocks(
    blocks: list[Block],
    options: Mapping[str, Any] | None = None,
) -> list[list[Block]]:
    """Split blocks into batches that fit Slack's size limits.

    Defaults: 40 blocks and 12,000 JSON characters per batch. Section text
    longer than 3,000 characters is chunked first. Header text longer than
    150 characters becomes a bold section, because that is Slack's header
    limit. A ``container`` is split into more containers with the same
    settings, at most 10 children each, titled ``"{title} (continued)"``.
    """
    options = options or {}
    max_blocks = options.get("max_blocks", options.get("maxBlocks", DEFAULT_MAX_BLOCKS))
    max_chars = options.get(
        "max_characters", options.get("maxCharacters", DEFAULT_MAX_CHARACTERS)
    )

    normalized: list[Block] = []
    for block in blocks:
        if block.get("type") == "section":
            normalized.extend(_split_section_block(block, DEFAULT_MAX_TEXT_SECTION_CHARACTERS))
        elif block.get("type") == "header":
            normalized.extend(_split_header_block(block, DEFAULT_MAX_TEXT_SECTION_CHARACTERS))
        elif block.get("type") == "container":
            normalized.extend(_split_container_block(block, max_chars))
        else:
            normalized.append(block)

    if not normalized:
        return [[]]

    if len(normalized) <= max_blocks and len(js_stringify(normalized)) <= max_chars:
        return [normalized]

    result: list[list[Block]] = []
    current: list[Block] = []

    def fits(batch: list[Block], new_block: Block) -> bool:
        if len(batch) + 1 > max_blocks:
            return False
        return len(js_stringify([*batch, new_block])) <= max_chars

    def flush() -> None:
        nonlocal current
        if current:
            result.append(current)
            current = []

    for block in normalized:
        if fits(current, block):
            current.append(block)
            continue
        flush()
        if fits([], block):
            current.append(block)
            continue
        if block.get("type") == "rich_text":
            for sub in _split_large_rich_text_block(block, max_chars):
                if fits(current, sub):
                    current.append(sub)
                else:
                    flush()
                    current.append(sub)
        else:
            current.append(block)

    flush()
    return result or [[]]


def split_blocks_with_text(
    blocks: list[Block],
    options: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Like ``split_blocks``, plus a plain-text fallback for each batch."""
    return [
        {"text": blocks_to_plain_text(batch), "blocks": batch}
        for batch in split_blocks(blocks, options)
    ]


def _split_large_rich_text_block(block: Block, max_chars: int) -> list[Block]:
    elements = block.get("elements") or []
    if not elements:
        return [block]
    result: list[Block] = []
    for element_block in _split_rich_text_by_elements(elements, max_chars):
        if len(js_stringify(element_block)) <= max_chars:
            result.append(element_block)
        else:
            result.extend(_split_rich_text_block_elements(element_block, max_chars))
    return result


def _split_rich_text_by_elements(elements: list[dict[str, Any]], max_chars: int) -> list[Block]:
    result: list[Block] = []
    current: list[dict[str, Any]] = []

    def create(elems: list[dict[str, Any]]) -> Block:
        return {"type": "rich_text", "elements": elems}

    for element in elements:
        test = create([*current, element])
        if len(js_stringify(test)) <= max_chars:
            current.append(element)
        else:
            if current:
                result.append(create(current))
                current = []
            current.append(element)
    if current:
        result.append(create(current))
    return result or [create([])]


def _split_rich_text_block_elements(block: Block, max_chars: int) -> list[Block]:
    result: list[Block] = []
    for element in block.get("elements") or []:
        if element.get("type") == "rich_text_preformatted":
            for split in _split_preformatted_element(element, max_chars):
                result.append({"type": "rich_text", "elements": [split]})
        else:
            result.append({"type": "rich_text", "elements": [element]})
    return result or [block]


def _split_preformatted_element(element: dict[str, Any], max_chars: int) -> list[dict[str, Any]]:
    text_elements = [item for item in element.get("elements") or [] if item.get("type") == "text"]
    if not text_elements:
        return [element]
    full_text = "".join(item.get("text", "") if item.get("type") == "text" else "" for item in text_elements)
    lines = full_text.split("\n")
    if len(lines) <= 1:
        return [element]

    def create(text: str) -> dict[str, Any]:
        created: dict[str, Any] = {
            "type": "rich_text_preformatted",
            "elements": [{"type": "text", "text": text}],
        }
        if element.get("border") is not None:
            created["border"] = element["border"]
        if element.get("language"):
            created["language"] = element["language"]
        return created

    result: list[dict[str, Any]] = []
    current: list[str] = []
    for line in lines:
        test_text = "\n".join([*current, line])
        if len(js_stringify(create(test_text))) <= max_chars:
            current.append(line)
        else:
            if current:
                result.append(create("\n".join(current)))
                current = []
            current.append(line)
    if current:
        result.append(create("\n".join(current)))
    return result or [element]


def _split_section_block(block: Block, max_chars: int) -> list[Block]:
    text = block.get("text")
    if not text or len(text.get("text") or "") <= max_chars:
        return [block]

    chunks = _chunk_string(text["text"], max_chars)
    result: list[Block] = []
    for index, chunk in enumerate(chunks):
        new_text = {**text, "text": chunk}
        new_block: Block = {"type": "section", "text": new_text}
        if index == 0:
            if block.get("block_id"):
                new_block["block_id"] = block["block_id"]
            if block.get("fields"):
                new_block["fields"] = block["fields"]
            if block.get("accessory"):
                new_block["accessory"] = block["accessory"]
        result.append(new_block)
    return result


def _split_header_block(block: Block, max_chars: int) -> list[Block]:
    text = block["text"]["text"]
    if len(text) <= SLACK_HEADER_MAX:
        return [block]
    return heading_text_to_sections(text, max_chars, block.get("block_id"))


def _split_container_block(block: Block, max_chars: int) -> list[Block]:
    children: list[Block] = []
    for child in coerce_container_children(list(block.get("child_blocks") or [])):
        children.extend(_normalize_container_child(child, max_chars))
    if not children:
        return [_container_shell(block, [], continued=False)]
    groups: list[list[Block]] = []
    current: list[Block] = []
    for child in children:
        continued = bool(groups)
        if current and (
            len(current) >= 10
            or len(js_stringify(_container_shell(block, [*current, child], continued=continued))) > max_chars
        ):
            groups.append(current)
            current = [child]
            continue
        current.append(child)
    if current:
        groups.append(current)
    return [
        _container_shell(block, group, continued=index > 0) for index, group in enumerate(groups)
    ]


def _normalize_container_child(child: Block, max_chars: int) -> list[Block]:
    kind = child.get("type")
    if kind == "section":
        return _split_section_block(child, DEFAULT_MAX_TEXT_SECTION_CHARACTERS)
    if kind == "header":
        return _split_header_block(child, DEFAULT_MAX_TEXT_SECTION_CHARACTERS)
    if kind == "rich_text" and len(js_stringify(child)) > max_chars:
        return _split_large_rich_text_block(child, max_chars)
    if kind == "container":
        return _split_container_block(child, max_chars)
    return [child]


def _container_shell(block: Block, children: list[Block], *, continued: bool) -> Block:
    shell: Block = {key: value for key, value in block.items() if key not in {"child_blocks", "block_id"}}
    shell["type"] = "container"
    shell["child_blocks"] = children
    if not continued and block.get("block_id"):
        shell["block_id"] = block["block_id"]
    if continued:
        _apply_continuation_title(shell)
    return shell


def _apply_continuation_title(block: Block) -> None:
    suffix = " (continued)"
    title = block.get("title")
    if isinstance(title, dict) and title.get("type") == "plain_text" and not block.get("rich_text_title"):
        text = str(title.get("text") or "")
        block["title"] = {**title, "text": _fit_plain_title(text, suffix)}
        return
    rich = block.get("rich_text_title")
    if isinstance(rich, dict):
        block["rich_text_title"] = _append_rich_text(rich, suffix)


def _fit_plain_title(text: str, suffix: str) -> str:
    if len(text) + len(suffix) <= SLACK_HEADER_MAX:
        return text + suffix
    keep = SLACK_HEADER_MAX - len(suffix)
    return text[: max(keep, 0)] + suffix


def _append_rich_text(block: dict[str, Any], suffix: str) -> dict[str, Any]:
    cloned = json.loads(js_stringify(block))
    elements = cloned.setdefault("elements", [])
    if not elements or elements[0].get("type") != "rich_text_section":
        elements.append(
            {"type": "rich_text_section", "elements": [{"type": "text", "text": suffix}]}
        )
        return cloned
    section = elements[0].setdefault("elements", [])
    if section and section[-1].get("type") == "text" and not section[-1].get("style"):
        section[-1]["text"] = str(section[-1].get("text") or "") + suffix
    else:
        section.append({"type": "text", "text": suffix})
    return cloned


def _chunk_string(value: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = value
    while current:
        if len(current) <= limit:
            chunks.append(current)
            break
        newline_index = current.rfind("\n", 0, limit + 1)
        if newline_index > 0:
            chunks.append(current[:newline_index])
            current = current[newline_index + 1 :]
            continue
        space_index = current.rfind(" ", 0, limit + 1)
        if space_index != -1 and space_index > limit * 0.8:
            chunks.append(current[:space_index])
            current = current[space_index + 1 :]
            continue
        chunks.append(current[:limit])
        current = current[limit:]
    return chunks


def blocks_to_markdown(
    blocks: list[Block],
    options: Mapping[str, Any] | None = None,
) -> str:
    """Best-effort Markdown for blocks produced by this library."""
    validate_blocks_to_markdown_options(options)
    parts = []
    for block in blocks:
        rendered = _render_block_as_markdown(block, options).strip()
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _render_block_as_markdown(block: Block, options: Mapping[str, Any] | None) -> str:
    kind = block.get("type")
    if kind == "section":
        return _render_section_as_markdown(block, options)
    if kind == "header":
        return f"# {block['text']['text']}"
    if kind == "context":
        bits = []
        for element in block.get("elements") or []:
            if element.get("type") == "image":
                bits.append(_render_image_element(element))
            else:
                rendered = _render_text_object(element, options)
                if rendered:
                    bits.append(rendered)
        return " ".join(bits)
    if kind == "rich_text":
        return _render_rich_text_block(block, options)
    if kind == "divider":
        return "---"
    if kind == "image":
        return _render_image_block(block)
    if kind == "table":
        return _render_table(block, options)
    if kind == "data_table":
        return _render_data_table(block, options)
    return ""


def _render_section_as_markdown(block: Block, options: Mapping[str, Any] | None) -> str:
    parts: list[str] = []
    text = _render_text_object(block.get("text"), options)
    if text:
        parts.append(text)
    fields = block.get("fields") or []
    rendered_fields = "\n".join(
        item for item in (_render_text_object(field, options) for field in fields) if item
    )
    if rendered_fields:
        parts.append(rendered_fields)
    accessory = block.get("accessory")
    if _is_image_element(accessory):
        parts.append(_render_image_element(accessory))
    return "\n\n".join(parts)


def _render_text_object(text: Mapping[str, Any] | None, options: Mapping[str, Any] | None) -> str:
    if not text:
        return ""
    if text.get("type") == "mrkdwn":
        return _convert_mrkdwn_to_markdown(text.get("text") or "", options)
    return text.get("text") or ""


def _render_rich_text_block(block: Block, options: Mapping[str, Any] | None) -> str:
    heading = _render_rich_text_heading(block)
    if heading:
        return heading
    return _render_rich_text_body(block, options)


def _render_rich_text_body(block: Block, options: Mapping[str, Any] | None) -> str:
    rendered = []
    for element in block.get("elements") or []:
        part = _render_rich_text_element(element, options)
        if part["markdown"]:
            rendered.append(part)
    if not rendered:
        return ""
    markdown = rendered[0]["markdown"]
    for index in range(1, len(rendered)):
        previous = rendered[index - 1]
        current = rendered[index]
        separator = "\n" if previous["kind"] == "list" and current["kind"] == "list" else "\n\n"
        markdown += separator + current["markdown"]
    return markdown


def _render_rich_text_heading(block: Block) -> str:
    elements = block.get("elements") or []
    if len(elements) != 1:
        return ""
    element = elements[0]
    if element.get("type") != "rich_text_section" or len(element.get("elements") or []) != 1:
        return ""
    text_element = element["elements"][0]
    style = text_element.get("style") or {}
    if (
        text_element.get("type") != "text"
        or not style.get("bold")
        or style.get("italic")
        or style.get("strike")
        or style.get("code")
    ):
        return ""
    return f"### {text_element['text']}"


def _render_rich_text_element(element: dict[str, Any], options: Mapping[str, Any] | None) -> dict[str, str]:
    kind = element.get("type")
    if kind == "rich_text_section":
        return {"kind": "section", "markdown": _render_rich_text_section(element, options)}
    if kind == "rich_text_list":
        return {"kind": "list", "markdown": _render_rich_text_list(element, options)}
    if kind == "rich_text_preformatted":
        return {"kind": "preformatted", "markdown": _render_preformatted(element, options)}
    if kind == "rich_text_quote":
        return {"kind": "quote", "markdown": _render_quote(element, options)}
    return {"kind": "section", "markdown": ""}


def _render_rich_text_section(section: dict[str, Any], options: Mapping[str, Any] | None) -> str:
    return "".join(
        _render_section_element(element, options) for element in section.get("elements") or []
    )


def _render_rich_text_list(lst: dict[str, Any], options: Mapping[str, Any] | None) -> str:
    indent_unit = "   " if lst.get("style") == "ordered" else "  "
    indent = indent_unit * int(lst.get("indent") or 0)
    start = int(lst.get("offset") or 1)
    lines = []
    for index, item in enumerate(lst.get("elements") or []):
        marker = f"{start + index}. " if lst.get("style") == "ordered" else "- "
        content = _render_rich_text_section(item, options)
        lines.append(_indent_multiline(f"{indent}{marker}", content))
    return "\n".join(lines)


def _render_preformatted(element: dict[str, Any], options: Mapping[str, Any] | None) -> str:
    text = "".join(
        _render_section_element(item, options) for item in element.get("elements") or []
    )
    return _wrap_fenced_code(text, element.get("language") or "")


def _render_quote(element: dict[str, Any], options: Mapping[str, Any] | None) -> str:
    rendered = _render_rich_text_section(
        {"type": "rich_text_section", "elements": element.get("elements") or []},
        options,
    )
    return "\n".join(f"> {line}" for line in rendered.split("\n"))


def _render_section_element(element: dict[str, Any], options: Mapping[str, Any] | None) -> str:
    kind = element.get("type")
    style = element.get("style")
    if kind == "text":
        return _apply_markdown_style(element.get("text") or "", style)
    if kind == "link":
        text = element.get("text") or ""
        content = f"[{text}](<{element.get('url')}>)" if text else (element.get("url") or "")
        return _apply_markdown_style(content, style)
    if kind == "emoji":
        return _apply_markdown_style(f":{element.get('name')}:", style)
    if kind == "date":
        fallback = element.get("fallback")
        if fallback is None:
            fallback = _iso(element.get("timestamp") or 0)
        content = f"<!date^{element.get('timestamp')}^{element.get('format')}|{fallback}>"
        return _apply_markdown_style(content, style)
    if kind == "user":
        return _apply_markdown_style(_render_user_mention(element.get("user_id") or "", options), style)
    if kind == "usergroup":
        return _apply_markdown_style(
            _render_user_group_mention(element.get("usergroup_id") or "", options),
            style,
        )
    if kind == "team":
        return _apply_markdown_style(_render_team_mention(element.get("team_id") or "", options), style)
    if kind == "channel":
        return _apply_markdown_style(
            _render_channel_mention(element.get("channel_id") or "", options),
            style,
        )
    if kind == "broadcast":
        return _apply_markdown_style(f"<!{element.get('range')}>", style)
    if kind == "color":
        return _apply_markdown_style(element.get("value") or "", style)
    return ""


def _render_image_block(block: Mapping[str, Any]) -> str:
    title = ""
    title_text = (block.get("title") or {}).get("text")
    if title_text:
        title = f' "{title_text.replace(chr(34), chr(92) + chr(34))}"'
    return f"![{block.get('alt_text')}](<{block.get('image_url')}>{title})"


def _render_image_element(element: Mapping[str, Any]) -> str:
    return f"![{element.get('alt_text')}](<{element.get('image_url')}>)"


def _render_table(block: Block, options: Mapping[str, Any] | None) -> str:
    rows = block.get("rows") or []
    if not rows or not rows[0]:
        return ""
    rendered = [
        "| " + " | ".join(_render_table_cell(cell, options) for cell in row) + " |"
        for row in rows
    ]
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    return "\n".join([rendered[0], separator, *rendered[1:]])


def _render_table_cell(cell: Block, options: Mapping[str, Any] | None) -> str:
    return (
        _render_rich_text_body(cell, options).replace("|", "\\|").replace("\n", "\\n")
    )


def _render_data_table(block: Block, options: Mapping[str, Any] | None) -> str:
    rows = block.get("rows") or []
    if not rows or not rows[0]:
        return ""
    rendered = [
        "| " + " | ".join(_render_data_cell(cell, options) for cell in row) + " |"
        for row in rows
    ]
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    return "\n".join([rendered[0], separator, *rendered[1:]])


def _render_data_cell(cell: Mapping[str, Any], options: Mapping[str, Any] | None) -> str:
    if cell.get("type") in ("raw_text", "raw_number"):
        rendered = cell.get("text") or ""
    else:
        rendered = _render_rich_text_body(cell, options)
    return rendered.replace("|", "\\|").replace("\n", "\\n")


def _apply_markdown_style(text: str, style: Mapping[str, Any] | None) -> str:
    if not style:
        return text
    result = text
    if style.get("code"):
        result = _wrap_inline_code(result)
    if style.get("bold"):
        result = f"**{result}**"
    if style.get("italic"):
        result = f"*{result}*"
    if style.get("strike"):
        result = f"~{result}~"
    return result


def _wrap_inline_code(text: str) -> str:
    fence = _backtick_fence(text, 1)
    return f"{fence}{text}{fence}"


def _wrap_fenced_code(text: str, language: str = "") -> str:
    fence = _backtick_fence(text, 3)
    return f"{fence}{language}\n{text}\n{fence}"


def _backtick_fence(text: str, minimum: int) -> str:
    runs = re.findall(r"`+", text)
    longest = max((len(run) for run in runs), default=0)
    return "`" * max(minimum, longest + 1)


def _indent_multiline(prefix: str, text: str) -> str:
    lines = text.split("\n")
    if not lines:
        return prefix.rstrip()
    continuation = " " * len(prefix)
    return "\n".join(
        f"{prefix}{line}" if index == 0 else f"{continuation}{line}"
        for index, line in enumerate(lines)
    )


def _convert_mrkdwn_to_markdown(text: str, options: Mapping[str, Any] | None) -> str:
    result: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "`":
            closing = text.find("`", index + 1)
            if closing != -1:
                result.append(_wrap_inline_code(_unescape_mrkdwn(text[index + 1 : closing])))
                index = closing + 1
                continue
        if text.startswith('&amp;', index):
            result.append('&')
            index += 5
            continue
        if text.startswith('&lt;', index):
            result.append('<')
            index += 4
            continue
        if text.startswith('&gt;', index):
            result.append('>')
            index += 4
            continue
        if character == "<":
            closing = text.find(">", index + 1)
            if closing != -1:
                result.append(_convert_angle_token(text[index : closing + 1], options))
                index = closing + 1
                continue
        if _is_style_marker(character) and _is_valid_style_open(text, index):
            closing = _find_closing_style_marker(text, index)
            if closing != -1:
                inner = _convert_mrkdwn_to_markdown(text[index + 1 : closing], options)
                result.append(_wrap_converted_style(character, inner))
                index = closing + 1
                continue
        result.append(character)
        index += 1
    return "".join(result)




def _unescape_mrkdwn(text: str) -> str:
    """Decode mrkdwn entities one pass, so a doubled amp stays an entity."""
    result: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith('&amp;', index):
            result.append('&')
            index += 5
        elif text.startswith('&lt;', index):
            result.append('<')
            index += 4
        elif text.startswith('&gt;', index):
            result.append('>')
            index += 4
        else:
            result.append(text[index])
            index += 1
    return "".join(result)

def _convert_angle_token(token: str, options: Mapping[str, Any] | None) -> str:
    user = re.fullmatch(r"<@([A-Za-z0-9_.-]+)>", token)
    if user:
        return _render_user_mention(user.group(1), options)
    channel = re.fullmatch(r"<#([A-Za-z0-9_.-]+)>", token)
    if channel:
        return _render_channel_mention(channel.group(1), options)
    subteam = re.fullmatch(r"<!subteam\^([A-Za-z0-9_.-]+)>", token)
    if subteam:
        subteam_id = subteam.group(1)
        if subteam_id.startswith("S"):
            return _render_user_group_mention(subteam_id, options)
        return _render_team_mention(subteam_id, options)
    if token.startswith("<!"):
        return token
    formatted = re.fullmatch(r"<([^|>]+)\|(.+)>", token)
    if formatted:
        url, label = formatted.group(1), formatted.group(2)
        url = _unescape_mrkdwn(url)
        return f"[{_convert_mrkdwn_to_markdown(label, options)}](<{url}>)"
    auto = re.fullmatch(r"<([^>]+)>", token)
    if auto:
        return auto.group(1)
    return token


def _is_style_marker(character: str) -> bool:
    return character in {"*", "_", "~"}


def _is_valid_style_open(text: str, index: int) -> bool:
    previous = text[index - 1] if index else None
    nxt = text[index + 1] if index + 1 < len(text) else None
    return _is_boundary(previous) and not _is_whitespace(nxt)


def _is_valid_style_close(text: str, index: int) -> bool:
    previous = text[index - 1] if index else None
    nxt = text[index + 1] if index + 1 < len(text) else None
    return not _is_whitespace(previous) and _is_boundary(nxt)


def _find_closing_style_marker(text: str, opening: int) -> int:
    marker = text[opening]
    index = opening + 1
    while index < len(text):
        character = text[index]
        if character == "`":
            closing = text.find("`", index + 1)
            if closing == -1:
                return -1
            index = closing + 1
            continue
        if character == "<":
            closing = text.find(">", index + 1)
            if closing == -1:
                return -1
            index = closing + 1
            continue
        if character == marker and _is_valid_style_close(text, index):
            return index
        index += 1
    return -1


def _wrap_converted_style(marker: str, text: str) -> str:
    if marker == "*":
        return f"**{text}**"
    if marker == "_":
        return f"*{text}*"
    return f"~{text}~"


def _is_boundary(character: str | None) -> bool:
    if character is None:
        return True
    return _BOUNDARY.fullmatch(character) is not None


def _is_whitespace(character: str | None) -> bool:
    return character is not None and _WS.fullmatch(character) is not None


def _mention_maps(options: Mapping[str, Any] | None) -> dict[str, Mapping[str, str]]:
    mentions = (options or {}).get("mentions") or {}
    def pick(*keys: str) -> Mapping[str, str]:
        for key in keys:
            value = mentions.get(key)
            if isinstance(value, Mapping):
                return value
        return {}
    return {
        "users": pick("users"),
        "channels": pick("channels"),
        "user_groups": pick("userGroups", "user_groups"),
        "teams": pick("teams"),
    }


def _render_named(name: str | None, fallback: str, prefix: str = "@") -> str:
    return f"{prefix}{name}" if name else fallback


def _render_user_mention(user_id: str, options: Mapping[str, Any] | None) -> str:
    maps = _mention_maps(options)
    name = maps["users"].get(user_id) or maps["user_groups"].get(user_id) or maps["teams"].get(user_id)
    return _render_named(name, f"<@{user_id}>")


def _render_channel_mention(channel_id: str, options: Mapping[str, Any] | None) -> str:
    name = _mention_maps(options)["channels"].get(channel_id)
    return _render_named(name, f"<#{channel_id}>", "#")


def _render_user_group_mention(user_group_id: str, options: Mapping[str, Any] | None) -> str:
    name = _mention_maps(options)["user_groups"].get(user_group_id)
    return _render_named(name, f"<!subteam^{user_group_id}>")


def _render_team_mention(team_id: str, options: Mapping[str, Any] | None) -> str:
    name = _mention_maps(options)["teams"].get(team_id)
    return _render_named(name, f"<!subteam^{team_id}>")


def _is_image_element(element: Any) -> bool:
    return (
        isinstance(element, dict)
        and element.get("type") == "image"
        and isinstance(element.get("image_url"), str)
        and isinstance(element.get("alt_text"), str)
    )


def _iso(timestamp: int | float) -> str:
    moment = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def blocks_to_plain_text(blocks: list[Block]) -> str:
    """Lightweight plain-text fallback for ``chat.postMessage``."""

    def render_section_element(element: Mapping[str, Any]) -> str:
        kind = element.get("type")
        if kind == "text":
            return element.get("text") or ""
        if kind == "link":
            return element.get("text") or element.get("url") or ""
        if kind == "emoji":
            return f":{element.get('name')}:"
        if kind == "date":
            return element.get("fallback") or _iso(element.get("timestamp") or 0)
        if kind == "user":
            return f"<@{element.get('user_id')}>"
        if kind == "usergroup":
            return f"<!subteam^{element.get('usergroup_id')}>"
        if kind == "team":
            return f"<team:{element.get('team_id')}>"
        if kind == "channel":
            return f"<#{element.get('channel_id')}>"
        if kind == "broadcast":
            return {
                "here": "<!here>",
                "channel": "<!channel>",
                "everyone": "<!everyone>",
            }.get(element.get("range") or "", "")
        if kind == "color":
            return element.get("value") or ""
        return ""

    def render_element(element: Mapping[str, Any]) -> str:
        kind = element.get("type")
        if kind == "rich_text_section":
            return "".join(
                part
                for part in (render_section_element(item) for item in element.get("elements") or [])
                if part
            )
        if kind == "rich_text_list":
            lines = []
            offset = int(element.get("offset") or 1)
            for index, item in enumerate(element.get("elements") or []):
                marker = f"{offset + index}. " if element.get("style") == "ordered" else "- "
                lines.append(
                    marker
                    + "".join(render_section_element(child) for child in item.get("elements") or [])
                )
            return "\n".join(lines)
        if kind == "rich_text_preformatted":
            return "".join(render_section_element(item) for item in element.get("elements") or [])
        if kind == "rich_text_quote":
            return "\n".join(
                f"> {render_section_element(item)}" for item in element.get("elements") or []
            )
        return ""

    def render_rich_text(block: Mapping[str, Any]) -> str:
        return "\n".join(
            part for part in (render_element(element) for element in block.get("elements") or []) if part
        )

    def render_block(block: Mapping[str, Any]) -> str:
        kind = block.get("type")
        if kind in ("section", "header"):
            return (block.get("text") or {}).get("text") or ""
        if kind == "context":
            return " ".join(
                part
                for part in ((element.get("text") or "") for element in block.get("elements") or [])
                if part
            )
        if kind == "rich_text":
            return render_rich_text(block)
        if kind == "divider":
            return "---"
        if kind == "image":
            title = (block.get("title") or {}).get("text")
            return title or block.get("alt_text") or "Image"
        if kind == "table":
            return "\n".join(
                " | ".join(render_rich_text(cell) for cell in row) for row in block.get("rows") or []
            )
        if kind == "data_table":
            lines = []
            for row in block.get("rows") or []:
                cells = []
                for cell in row:
                    if cell.get("type") in ("raw_text", "raw_number"):
                        cells.append(cell.get("text") or "")
                    else:
                        cells.append(render_rich_text(cell))
                lines.append(" | ".join(cells))
            return "\n".join(lines)
        if kind == "container":
            title = (block.get("title") or {}).get("text") or ""
            if title:
                return title
            rich = block.get("rich_text_title")
            if isinstance(rich, dict):
                return render_rich_text(rich)
            return ""
        return ""

    parts = []
    for block in blocks:
        rendered = render_block(block).strip()
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)
