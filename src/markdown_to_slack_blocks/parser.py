"""Markdown → Slack Block Kit."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from markdown_it import MarkdownIt
from markdown_it.common.utils import unescapeAll
from markdown_it.rules_inline import StateInline

from .tags import apply_xml_tag_replacements, extract_xml_tags, resolve_xml_tag_handlers
from .validator import validate_options

Block = dict[str, Any]
Node = dict[str, Any]

_INLINE_TYPES = {"text", "html", "emphasis", "strong", "delete", "inlineCode", "link", "image"}

# Slack's own angle tokens may carry a label (`<@U123|name>`); it is dropped on output.
_LABEL = r"(?:\|[^<>\n]*)?"

# Same token order as the JavaScript scanner. ASCII so \w / \d match JS.
_TOKEN_RE = re.compile(
    rf"<!(?P<broadcast>here|channel|everyone){_LABEL}>"
    rf"|<@(?P<user>[A-Za-z0-9_.-]+){_LABEL}>"
    r"|(?P<color>#[0-9a-fA-F]{6})"
    rf"|<#(?P<channel>[A-Za-z0-9_.-]+){_LABEL}>"
    rf"|<!subteam\^(?P<subteam>[A-Za-z0-9_.-]+){_LABEL}>"
    r"|<!date\^(?P<date>[0-9]+)\^(?P<date_format>[^|]+)\|[^>]+>"
    r"|:(?P<emoji>[A-Za-z0-9_+-]+):"
    r"|@(?P<at_name>[A-Za-z0-9_.-]+)"
    r"|#(?P<hash_name>[A-Za-z0-9_.-]+)",
)
_SLACK_TOKEN = re.compile(r"<(?:[@#]|!(?:here|channel|everyone|subteam\^|date\^))[^<>\n]*>")
# `\|` is accepted too: table rows escape the pipe (see preprocess_markdown).
_SLACK_LINK = re.compile(r"<(?P<url>[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>|\\]+)\\?\|(?P<label>[^<>\n]*)>")
_SLACK_ANGLE = re.compile(f"{_SLACK_LINK.pattern}|{_SLACK_TOKEN.pattern}")
_TABLE_DELIMITER = re.compile(r"[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")
_QUOTE_PREFIX = re.compile(r"(?:[ ]{0,3}>[ ]?)*")
# Lines that end a GFM table without a blank line: a heading, list item, or thematic break.
_BLOCK_START = re.compile(r"[ ]{0,3}(?:#{1,6}(?:[ \t]|$)|[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|(?:[-*_][ \t]*){3,}$)")
_TEXT_NODES = ("paragraph", "heading", "html")

_HEADING_WRAPPED_LIST = re.compile(
    r"^(#{1,6})\s+(\*\*|\*|_|~)(\d+\.|\*|-|\+)(\s+)(.*?)\2$"
)
_WRAPPED_LIST = re.compile(r"^(\*\*|\*|_|~)(\d+\.|\*|-|\+)(\s+)(.*?)\1$")
_TASK_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\[[ xX]\]\s?(.*)$")
_FENCE_OPEN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})")
_NUMERIC = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")

# Slack data_table limits: header counts as a row, and cell text is summed.
DATA_TABLE_MIN_ROWS = 2
DATA_TABLE_MAX_ROWS = 201
DATA_TABLE_MAX_COLUMNS = 20
DATA_TABLE_MAX_CELL_CHARACTERS = 20_000
EMPTY_DATA_TABLE_CELL = " "
SLACK_HEADER_MAX = 150


def _slack_angle(state: StateInline, silent: bool) -> bool:
    """Read Slack's ``<url|label>`` links and ``<@U…>``-style tokens as written.

    CommonMark reads them as text, HTML, or an email autolink, which escapes or mangles them.
    """
    if state.src[state.pos] != "<":
        return False
    token = _SLACK_TOKEN.match(state.src, state.pos)
    text = unescapeAll(token.group(0)) if token else ""
    if token and _TOKEN_RE.fullmatch(text):
        if not silent:
            state.pending += text
        state.pos = token.end()
        return True
    link = _SLACK_LINK.match(state.src, state.pos)
    if not link:
        return False
    url = state.md.normalizeLink(unescapeAll(link["url"]))
    if not state.md.validateLink(url):
        return False
    if not silent:
        state.push("link_open", "a", 1).attrs = {"href": url}
        label = unescapeAll(link["label"]).strip()
        state.push("text", "", 0).content = label or state.md.normalizeLinkText(url)
        state.push("link_close", "a", -1)
    state.pos = link.end()
    return True


_MD = MarkdownIt(
    "commonmark",
    {"html": True, "linkify": True, "strikethrough_single_tilde": True},
).enable(["strikethrough", "table", "linkify"])
_MD.inline.ruler.before("autolink", "slack_angle", _slack_angle)
# Only URLs with a scheme become links. Fuzzy matching would link file names such as `settings.py`.
_MD.linkify.set({"fuzzy_link": False, "fuzzy_email": False, "fuzzy_ip": False})
_MD.linkify.add("//", None)


def markdown_to_blocks(
    markdown: str,
    options: Mapping[str, Any] | None = None,
) -> list[Block]:
    """Convert a Markdown string into Slack Block Kit blocks.

    Options (snake_case or the original camelCase keys):

    * ``mentions`` — ``users``, ``channels``, ``user_groups`` / ``userGroups``,
      ``teams`` maps from display name to Slack ID.
    * ``detect_colors`` / ``detectColors`` — hex colors become ``color``
      elements. Default ``True``.
    * ``prefer_section_blocks`` / ``preferSectionBlocks`` — paragraphs and
      H3+ headings become ``section`` blocks. Default ``True``.
    * ``table_block_type`` / ``tableBlockType`` — ``"data_table"`` (default)
      or legacy ``"table"``.
    * ``table_caption`` / ``tableCaption`` — caption for ``data_table``.
      Default ``"Data table"``. Pass ``""`` to omit it.
    * ``xml_tag_handlers`` / ``xmlTagHandlers`` — map of XML element name to a
      function ``(XmlTagContext) -> block | list[block] | None``. Names are
      case-sensitive. Expat parses the tags; the inner Markdown is passed
      through unchanged. Use this to wrap ``<sources>`` or ``<detailed>`` in
      a ``container``.
    """
    validate_options(options)
    options = options or {}
    handlers = resolve_xml_tag_handlers(options)
    if not handlers:
        return _parse_markdown(markdown, options)

    prepared, replacements = extract_xml_tags(markdown, set(handlers))

    def convert(source: str) -> list[Block]:
        return markdown_to_blocks(source, options)

    blocks = _parse_markdown(prepared, options)
    return apply_xml_tag_replacements(blocks, replacements, handlers, options, convert)


def _flag(options: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in options:
            return options[name]
    return default


def _mentions(options: Mapping[str, Any]) -> Mapping[str, Any]:
    mentions = options.get("mentions") or {}
    return mentions if isinstance(mentions, Mapping) else {}


def _named_map(mentions: Mapping[str, Any], *keys: str) -> Mapping[str, str]:
    for key in keys:
        value = mentions.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def preprocess_markdown(markdown: str) -> str:
    """Unwrap lists that are fully wrapped in emphasis, strip task boxes, and keep Slack links in table cells."""
    lines = markdown.split("\n")
    processed: list[str] = []
    in_fence = False
    in_table = False
    table_depth = 0
    fence_char = ""
    fence_len = 0

    for index, line in enumerate(lines):
        fence = _FENCE_OPEN.match(line)
        if not in_fence and fence:
            in_fence = True
            in_table = False
            marker = fence.group(2)
            fence_char = marker[0]
            fence_len = len(marker)
            processed.append(line)
            continue
        if in_fence:
            if re.match(rf"^(\s{{0,3}}){fence_char}{{{fence_len},}}\s*$", line):
                in_fence = False
            processed.append(line)
            continue

        depth, body = _quote_depth(line)
        if not body.strip() or (in_table and (depth != table_depth or _BLOCK_START.match(body))):
            in_table = False
        if not in_table and "|" in body and index + 1 < len(lines):
            next_depth, delimiter = _quote_depth(lines[index + 1])
            in_table = next_depth == depth and "|" in delimiter and _TABLE_DELIMITER.fullmatch(delimiter) is not None
            table_depth = depth
        if in_table:
            # A table row splits cells on every unescaped `|`, including the one in `<url|label>`.
            line = _SLACK_ANGLE.sub(lambda match: match.group(0).replace("|", "\\|"), line)

        trimmed = line.strip()
        heading_match = _HEADING_WRAPPED_LIST.match(trimmed)
        if heading_match:
            _heading, fmt, marker, spaces, content = heading_match.groups()
            processed.append(f"{marker}{spaces}{fmt}{content}{fmt}")
            continue

        wrapped = _WRAPPED_LIST.match(trimmed)
        if wrapped:
            fmt, marker, spaces, content = wrapped.groups()
            leading = re.match(r"^\s*", line).group(0)  # type: ignore[union-attr]
            processed.append(f"{leading}{marker}{spaces}{fmt}{content}{fmt}")
            continue

        task = _TASK_ITEM.match(line)
        if task:
            indent, marker, rest = task.groups()
            processed.append(f"{indent}{marker} {rest}" if rest else f"{indent}{marker}")
            continue

        processed.append(line)

    return "\n".join(processed)


def _quote_depth(line: str) -> tuple[int, str]:
    """How many ``>`` markers quote a line, and the line without them."""
    match = _QUOTE_PREFIX.match(line)
    end = match.end() if match else 0
    return line[:end].count(">"), line[end:]


def _attr(token: Any, name: str) -> str | None:
    attrs = token.attrs
    if not attrs:
        return None
    if isinstance(attrs, dict):
        return attrs.get(name)
    for key, value in attrs:
        if key == name:
            return value
    return None


def _parse_inline(children: list[Any] | None) -> list[Node]:
    if not children:
        return []
    root: list[Node] = []
    stack: list[list[Node]] = []
    current = root
    open_type = {
        "strong_open": "strong",
        "em_open": "emphasis",
        "s_open": "delete",
        "link_open": "link",
    }
    close_types = {"strong_close", "em_close", "s_close", "link_close"}

    for token in children:
        kind = token.type
        if kind in open_type:
            node: Node = {"type": open_type[kind], "children": []}
            if kind == "link_open":
                node["url"] = _attr(token, "href") or ""
            current.append(node)
            stack.append(current)
            current = node["children"]
            continue
        if kind in close_types:
            if stack:
                current = stack.pop()
            continue
        if kind == "text":
            if not token.content:
                continue
            if current and current[-1]["type"] == "text":
                current[-1]["value"] += token.content
            else:
                current.append({"type": "text", "value": token.content})
            continue
        if kind == "softbreak":
            if current and current[-1]["type"] == "text":
                current[-1]["value"] += "\n"
            else:
                current.append({"type": "text", "value": "\n"})
            continue
        if kind == "hardbreak":
            current.append({"type": "break"})
            continue
        if kind == "code_inline":
            current.append({"type": "inlineCode", "value": token.content})
            continue
        if kind == "html_inline":
            current.append({"type": "html", "value": token.content})
            continue
        if kind == "image":
            current.append(
                {
                    "type": "image",
                    "url": _attr(token, "src") or "",
                    "alt": token.content or "",
                }
            )
            continue
    return root


def _code_value(content: str) -> str:
    if content.endswith("\n"):
        return content[:-1]
    return content


def _parse_table(tokens: list[Any], index: int) -> tuple[Node, int]:
    index += 1
    rows: list[Node] = []
    while index < len(tokens) and tokens[index].type != "table_close":
        if tokens[index].type == "tr_open":
            index += 1
            cells: list[Node] = []
            while index < len(tokens) and tokens[index].type != "tr_close":
                if tokens[index].type in ("th_open", "td_open"):
                    inline = tokens[index + 1]
                    cells.append(
                        {
                            "type": "tableCell",
                            "children": _parse_inline(inline.children),
                        }
                    )
                    index += 3
                else:
                    index += 1
            rows.append({"type": "tableRow", "children": cells})
            index += 1
        else:
            index += 1
    return {"type": "table", "children": rows}, index + 1


def _parse_list(tokens: list[Any], index: int) -> tuple[Node, int]:
    start = int(_attr(tokens[index], "start") or 1)
    ordered = tokens[index].type == "ordered_list_open"
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    index += 1
    items: list[Node] = []
    while index < len(tokens) and tokens[index].type != close_type:
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        index += 1
        children: list[Node] = []
        while index < len(tokens) and tokens[index].type != "list_item_close":
            if tokens[index].type in ("bullet_list_open", "ordered_list_open"):
                nested, index = _parse_list(tokens, index)
                children.append(nested)
            else:
                child, index = _parse_block(tokens, index)
                if child is not None:
                    children.append(child)
        items.append({"type": "listItem", "children": children})
        index += 1
    return {"type": "list", "ordered": ordered, "start": start, "children": items}, index + 1


def _parse_container(tokens: list[Any], index: int, close_type: str) -> tuple[list[Node], int]:
    index += 1
    children: list[Node] = []
    while index < len(tokens) and tokens[index].type != close_type:
        child, index = _parse_block(tokens, index)
        if child is not None:
            children.append(child)
    return children, index + 1


def _parse_block(tokens: list[Any], index: int) -> tuple[Node | None, int]:
    token = tokens[index]
    kind = token.type
    if kind == "heading_open":
        depth = int(token.tag[1])
        inline = tokens[index + 1]
        return (
            {
                "type": "heading",
                "depth": depth,
                "children": _parse_inline(inline.children),
            },
            index + 3,
        )
    if kind == "paragraph_open":
        inline = tokens[index + 1]
        return (
            {"type": "paragraph", "children": _parse_inline(inline.children)},
            index + 3,
        )
    if kind in ("bullet_list_open", "ordered_list_open"):
        return _parse_list(tokens, index)
    if kind in ("fence", "code_block"):
        info = (getattr(token, "info", None) or "").strip()
        language = info.split(None, 1)[0] if info else ""
        node = {"type": "code", "value": _code_value(token.content)}
        if language:
            node["language"] = language
        return node, index + 1
    if kind == "blockquote_open":
        children, index = _parse_container(tokens, index, "blockquote_close")
        return {"type": "blockquote", "children": children}, index
    if kind == "hr":
        return {"type": "thematicBreak"}, index + 1
    if kind == "html_block":
        return {"type": "html", "value": _code_value(token.content)}, index + 1
    if kind == "table_open":
        return _parse_table(tokens, index)
    return None, index + 1


def _markdown_to_ast(markdown: str) -> list[Node]:
    tokens = _MD.parse(preprocess_markdown(markdown))
    nodes: list[Node] = []
    index = 0
    while index < len(tokens):
        node, index = _parse_block(tokens, index)
        if node is not None:
            nodes.append(node)
    return nodes


class _BlockWriter:
    """Collects blocks. Consecutive rich text elements share one ``rich_text`` block."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self._elements: list[dict[str, Any]] = []

    def element(self, element: dict[str, Any]) -> None:
        self._elements.append(element)

    def block(self, *blocks: Block) -> None:
        self.flush()
        self.blocks.extend(blocks)

    def flush(self) -> None:
        if self._elements:
            self.blocks.append({"type": "rich_text", "elements": self._elements})
            self._elements = []


def _parse_markdown(markdown: str, options: Mapping[str, Any]) -> list[Block]:
    out = _BlockWriter()
    for node in _markdown_to_ast(markdown):
        _render_node(node, out, options)
    out.flush()
    return out.blocks


def _render_node(node: Node, out: _BlockWriter, options: Mapping[str, Any]) -> None:
    kind = node["type"]
    if kind == "heading":
        out.block(*_heading_blocks(node, options))
    elif kind == "paragraph":
        children = node["children"]
        if len(children) == 1 and children[0]["type"] == "image":
            image = children[0]
            out.block({"type": "image", "image_url": image["url"], "alt_text": image["alt"] or "Image"})
        elif _flag(options, "prefer_section_blocks", "preferSectionBlocks", default=True) is not False:
            out.block(_mrkdwn_section(_inlines_to_mrkdwn(children, options)))
        else:
            out.element({"type": "rich_text_section", "elements": _map_inlines(children, options)})
    elif kind == "list":
        _render_list(node, out, options, indent=0, quoted=False)
        out.flush()
    elif kind == "code":
        element: dict[str, Any] = {
            "type": "rich_text_preformatted",
            "elements": [{"type": "text", "text": node["value"]}],
        }
        if node.get("language"):
            element["language"] = node["language"]
        out.element(element)
        out.flush()
    elif kind == "blockquote":
        _render_quote(node, out, options)
        out.flush()
    elif kind == "thematicBreak":
        out.block({"type": "divider"})
    elif kind == "table":
        out.block(*_table_blocks(node, options))
    elif kind == "html":
        out.element({"type": "rich_text_section", "elements": _process_text(node["value"], {}, options)})


def _render_list(
    node: Node,
    out: _BlockWriter,
    options: Mapping[str, Any],
    indent: int,
    quoted: bool,
) -> None:
    """Render a list. Content other than text in an item, such as code, splits the list around it."""
    style = "ordered" if node["ordered"] else "bullet"
    entries: list[dict[str, Any]] = []
    first = number = node["start"]

    def flush_entries() -> None:
        nonlocal entries
        if not entries:
            return
        element: dict[str, Any] = {"type": "rich_text_list", "style": style, "indent": indent, "elements": entries}
        if style == "ordered" and first > 1:
            # Slack numbers from offset + 1, so a list split around other content keeps counting.
            element["offset"] = first - 1
        if quoted:
            element["border"] = 1
        out.element(element)
        entries = []

    for item in node["children"]:
        listed = False
        for run in _text_runs(item["children"]):
            if isinstance(run, list):
                elements = _join_lines(_text_node_elements(child, options) for child in run)
                if not elements:
                    continue
                if listed:
                    # Text after a code block or nested list continues the item without a new marker.
                    flush_entries()
                    out.element(_text_element(elements, quoted))
                else:
                    if not entries:
                        first = number
                    entries.append({"type": "rich_text_section", "elements": elements})
            elif run["type"] == "list":
                flush_entries()
                _render_list(run, out, options, indent + 1, quoted)
            else:
                flush_entries()
                _render_node(run, out, options)
            listed = True
        number += 1
    flush_entries()


def _render_quote(node: Node, out: _BlockWriter, options: Mapping[str, Any]) -> None:
    """Render a quote. A Slack quote holds only text, so a quoted list gets a quote border instead."""
    for run in _text_runs(_quote_children(node)):
        if isinstance(run, list):
            elements = _join_lines(_text_node_elements(child, options) for child in run)
            if elements:
                out.element(_text_element(elements, quoted=True))
        elif run["type"] == "list":
            _render_list(run, out, options, indent=0, quoted=True)
        else:
            _render_node(run, out, options)


def _quote_children(node: Node) -> list[Node]:
    """A quote's children with nested quotes flattened, because Slack cannot nest quotes."""
    children: list[Node] = []
    for child in node["children"]:
        if child["type"] == "blockquote":
            children.extend(_quote_children(child))
        else:
            children.append(child)
    return children


def _text_runs(children: list[Node]) -> list[list[Node] | Node]:
    """Group consecutive paragraphs, headings, and HTML; every other node stands alone."""
    runs: list[list[Node] | Node] = []
    for child in children:
        if child["type"] not in _TEXT_NODES:
            runs.append(child)
        elif runs and isinstance(runs[-1], list):
            runs[-1].append(child)
        else:
            runs.append([child])
    return runs


def _text_node_elements(node: Node, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    if node["type"] == "paragraph":
        return _map_inlines(node["children"], options)
    if node["type"] == "heading":
        return _flatten_styles(node["children"], {"bold": True}, options)
    return _process_text(node["value"], {}, options)


def _join_lines(parts: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Join the paragraphs of one list item or quote with line breaks."""
    joined: list[dict[str, Any]] = []
    for part in parts:
        if not part:
            continue
        if joined:
            joined.append({"type": "text", "text": "\n"})
        joined.extend(part)
    return joined


def _text_element(elements: list[dict[str, Any]], quoted: bool) -> dict[str, Any]:
    return {"type": "rich_text_quote" if quoted else "rich_text_section", "elements": elements}


def _table_blocks(node: Node, options: Mapping[str, Any]) -> list[Block]:
    cell_elements = [
        [_map_inlines(cell["children"], options) for cell in row["children"]]
        for row in node["children"]
    ]
    if _flag(options, "table_block_type", "tableBlockType") == "table":
        rows = [
            [
                {
                    "type": "rich_text",
                    "elements": [{"type": "rich_text_section", "elements": elements}],
                }
                for elements in row
            ]
            for row in cell_elements
        ]
        return [{"type": "table", "rows": rows}]
    rows = [[_build_data_table_cell(elements) for elements in row] for row in cell_elements]
    caption = _flag(options, "table_caption", "tableCaption", default="Data table")
    return expand_data_table(rows, caption if caption else None)


def _mrkdwn_section(text: str) -> Block:
    # verbatim: otherwise Slack links text such as `settings.py` on its own, even inside code spans.
    return {"type": "section", "text": {"type": "mrkdwn", "text": text, "verbatim": True}}


def _heading_blocks(node: Node, options: Mapping[str, Any]) -> list[Block]:
    text = _node_to_string(node)
    prefer_section = _flag(options, "prefer_section_blocks", "preferSectionBlocks", default=True) is not False
    if node["depth"] <= 2 and len(text) <= SLACK_HEADER_MAX:
        return [{"type": "header", "text": {"type": "plain_text", "text": text}}]
    if prefer_section:
        return [_mrkdwn_section(f"*{_inlines_to_mrkdwn(node['children'], options)}*")]
    return [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": text, "style": {"bold": True}}],
                }
            ],
        }
    ]


def escape_mrkdwn(text: str) -> str:
    """Escape Slack mrkdwn control characters. Ampersand is escaped first."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def heading_text_to_sections(text: str, max_chars: int, block_id: str | None = None) -> list[Block]:
    """Bold mrkdwn sections used when a header is longer than Slack allows."""
    inner_limit = max(1, max_chars - 2)
    chunks = _chunk_heading(text, inner_limit)
    blocks: list[Block] = []
    for index, chunk in enumerate(chunks):
        block = _mrkdwn_section(f"*{escape_mrkdwn(chunk)}*")
        if index == 0 and block_id:
            block["block_id"] = block_id
        blocks.append(block)
    return blocks


def _chunk_heading(text: str, limit: int) -> list[str]:
    """Local chunker so the parser does not import the splitter."""
    chunks: list[str] = []
    current = text
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
    return chunks or [text]


def _build_data_table_cell(elements: list[dict[str, Any]]) -> dict[str, Any]:
    if _elements_are_blank(elements):
        return {"type": "raw_text", "text": EMPTY_DATA_TABLE_CELL}
    if len(elements) == 1:
        element = elements[0]
        style = element.get("style") or {}
        if element["type"] == "text" and not style:
            text = element["text"]
            numeric = _parse_numeric(text)
            if numeric is not None:
                return {"type": "raw_number", "value": numeric, "text": text}
            return {"type": "raw_text", "text": text}
    return {
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": elements}],
    }


def _elements_are_blank(elements: list[dict[str, Any]]) -> bool:
    if not elements:
        return True
    return all(element.get("type") == "text" and not (element.get("text") or "") for element in elements)


def _cell_plain_text(cell: Mapping[str, Any]) -> str:
    kind = cell.get("type")
    if kind in ("raw_text", "raw_number"):
        return str(cell.get("text") or "")
    if kind != "rich_text":
        return ""
    parts: list[str] = []
    for element in cell.get("elements") or []:
        for item in element.get("elements") or []:
            if item.get("type") == "text":
                parts.append(item.get("text") or "")
    return "".join(parts)


def _row_text_length(row: list[Mapping[str, Any]]) -> int:
    return sum(len(_cell_plain_text(cell)) for cell in row)


def expand_data_table(rows: list[list[dict[str, Any]]], caption: str | None = None) -> list[Block]:
    """Turn parsed rows into Slack tables that fit ``data_table`` limits.

    Fewer than two rows, or more than 20 columns, becomes one legacy ``table``.
    Too many rows or cell characters are split, repeating the header. A single
    header-plus-row that is already over the character limit becomes a legacy
    ``table`` as well.
    """
    if not rows:
        return []
    width = max((len(row) for row in rows), default=0)
    if width > DATA_TABLE_MAX_COLUMNS or len(rows) < DATA_TABLE_MIN_ROWS:
        return [_legacy_table(rows)]

    header = rows[0]
    body = rows[1:]
    blocks: list[Block] = []
    current: list[list[dict[str, Any]]] = [header]
    current_chars = _row_text_length(header)

    def flush() -> None:
        nonlocal current, current_chars
        if len(current) >= DATA_TABLE_MIN_ROWS:
            blocks.append(_data_table_block(current, caption))
        current = [header]
        current_chars = _row_text_length(header)

    for row in body:
        row_chars = _row_text_length(row)
        if len(current) >= DATA_TABLE_MIN_ROWS and (
            len(current) + 1 > DATA_TABLE_MAX_ROWS
            or current_chars + row_chars > DATA_TABLE_MAX_CELL_CHARACTERS
        ):
            flush()
        if len(current) == 1 and current_chars + row_chars > DATA_TABLE_MAX_CELL_CHARACTERS:
            blocks.append(_legacy_table([header, row]))
            continue
        current.append(row)
        current_chars += row_chars
        if len(current) >= DATA_TABLE_MAX_ROWS:
            flush()
    if len(current) >= DATA_TABLE_MIN_ROWS:
        blocks.append(_data_table_block(current, caption))
    return blocks or [_legacy_table(rows)]


def _data_table_block(rows: list[list[dict[str, Any]]], caption: str | None) -> Block:
    block: Block = {"type": "data_table", "rows": rows}
    if caption:
        block["caption"] = caption
    return block


def _legacy_table(rows: list[list[dict[str, Any]]]) -> Block:
    return {"type": "table", "rows": [[_data_cell_to_table_cell(cell) for cell in row] for row in rows]}


def data_table_to_table(block: Block) -> Block:
    """Rewrite a ``data_table`` as the legacy ``table`` block containers allow."""
    table = _legacy_table(block.get("rows") or [])
    if block.get("block_id"):
        table["block_id"] = block["block_id"]
    return table


def coerce_container_children(children: list[Block]) -> list[Block]:
    """Replace ``data_table`` children with ``table`` blocks Slack will accept."""
    coerced: list[Block] = []
    for child in children:
        if child.get("type") != "data_table":
            coerced.append(child)
            continue
        for block in expand_data_table(child.get("rows") or [], child.get("caption")):
            if block.get("type") == "data_table":
                coerced.append(data_table_to_table(block))
            else:
                coerced.append(block)
    return coerced


def _data_cell_to_table_cell(cell: Mapping[str, Any]) -> dict[str, Any]:
    if cell.get("type") == "rich_text":
        return dict(cell)
    text = _cell_plain_text(cell) or EMPTY_DATA_TABLE_CELL
    return {
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}],
    }


def _parse_numeric(text: str) -> int | float | None:
    trimmed = text.strip()
    if not trimmed or not _NUMERIC.fullmatch(trimmed):
        return None
    value = float(trimmed)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if value.is_integer():
        return int(value)
    return value


def _map_inlines(nodes: list[Node], options: Mapping[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for node in nodes:
        if node["type"] in _INLINE_TYPES:
            elements.extend(_map_inline(node, options))
    return elements


def _map_inline(node: Node, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = node["type"]
    if kind in ("text", "html"):
        return _process_text(node["value"], {}, options)
    if kind == "emphasis":
        return _flatten_styles(node["children"], {"italic": True}, options)
    if kind == "strong":
        return _flatten_styles(node["children"], {"bold": True}, options)
    if kind == "delete":
        return _flatten_styles(node["children"], {"strike": True}, options)
    if kind == "inlineCode":
        return _process_text(node["value"], {"code": True}, options)
    if kind == "link":
        return [{"type": "link", "url": node["url"], "text": _node_to_string(node)}]
    if kind == "image":
        return [{"type": "link", "url": node["url"], "text": node["alt"] or "Image"}]
    return []


def _flatten_styles(
    children: list[Node],
    style: dict[str, bool],
    options: Mapping[str, Any],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for element in _map_inlines(children, options):
        combined = {**(element.get("style") or {}), **style}
        rest = {key: value for key, value in element.items() if key != "style"}
        if combined:
            rest["style"] = combined
        merged.append(rest)
    return merged


def _with_style(element: dict[str, Any], style: Mapping[str, bool]) -> dict[str, Any]:
    if style:
        return {**element, "style": dict(style)}
    return element


def _process_text(
    text: str,
    style: Mapping[str, bool],
    options: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if style.get("code"):
        literal: dict[str, Any] = {"type": "text", "text": text}
        if style:
            literal["style"] = dict(style)
        return [literal]

    mentions = _mentions(options)
    users = _named_map(mentions, "users")
    channels = _named_map(mentions, "channels")
    user_groups = _named_map(mentions, "userGroups", "user_groups")
    teams = _named_map(mentions, "teams")
    detect_colors = _flag(options, "detect_colors", "detectColors", default=True) is not False

    elements: list[dict[str, Any]] = []

    def add_text(value: str) -> None:
        if not value:
            return
        element: dict[str, Any] = {"type": "text", "text": value}
        if style:
            element["style"] = dict(style)
        elements.append(element)

    last = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > last:
            add_text(text[last : match.start()])
        full = match.group(0)
        if match["broadcast"]:
            elements.append(_with_style({"type": "broadcast", "range": match["broadcast"]}, style))
        elif match["user"]:
            elements.append(_with_style({"type": "user", "user_id": match["user"]}, style))
        elif match["color"]:
            if detect_colors:
                elements.append(_with_style({"type": "color", "value": match["color"]}, style))
            else:
                add_text(full)
        elif match["channel"]:
            elements.append(_with_style({"type": "channel", "channel_id": match["channel"]}, style))
        elif match["subteam"]:
            subteam_id = match["subteam"]
            if subteam_id.startswith("S"):
                elements.append(
                    _with_style({"type": "usergroup", "usergroup_id": subteam_id}, style)
                )
            else:
                elements.append(_with_style({"type": "team", "team_id": subteam_id}, style))
        elif match["date"]:
            elements.append(
                _with_style(
                    {
                        "type": "date",
                        "timestamp": int(match["date"]),
                        "format": match["date_format"],
                    },
                    style,
                )
            )
        elif match["emoji"]:
            elements.append(_with_style({"type": "emoji", "name": match["emoji"]}, style))
        elif match["at_name"]:
            name = match["at_name"]
            if name in ("here", "channel", "everyone"):
                elements.append(_with_style({"type": "broadcast", "range": name}, style))
            elif name in users:
                elements.append(_with_style({"type": "user", "user_id": users[name]}, style))
            elif name in user_groups:
                elements.append(
                    _with_style(
                        {"type": "usergroup", "usergroup_id": user_groups[name]},
                        style,
                    )
                )
            elif name in teams:
                elements.append(_with_style({"type": "team", "team_id": teams[name]}, style))
            else:
                add_text(full)
        elif match["hash_name"]:
            name = match["hash_name"]
            if name in channels:
                elements.append(
                    _with_style({"type": "channel", "channel_id": channels[name]}, style)
                )
            else:
                add_text(full)
        last = match.end()

    if last < len(text):
        add_text(text[last:])
    return elements


def _node_to_string(node: Node) -> str:
    kind = node["type"]
    if kind in ("text", "inlineCode", "html"):
        return node.get("value") or ""
    if kind == "image":
        return node.get("alt") or ""
    return "".join(_node_to_string(child) for child in node.get("children", []))


def _inlines_to_mrkdwn(nodes: list[Node], options: Mapping[str, Any]) -> str:
    return "".join(
        _inline_to_mrkdwn(node, options) for node in nodes if node["type"] in _INLINE_TYPES
    )


def _inline_to_mrkdwn(node: Node, options: Mapping[str, Any]) -> str:
    kind = node["type"]
    if kind == "text":
        return _text_to_mrkdwn(node["value"], options)
    if kind == "html":
        match = _TOKEN_RE.fullmatch(node["value"])
        canonical = _canonical_token(match) if match else None
        return canonical if canonical is not None else escape_mrkdwn(node["value"])
    if kind == "emphasis":
        return f"_{_inlines_to_mrkdwn(node['children'], options)}_"
    if kind == "strong":
        return f"*{_inlines_to_mrkdwn(node['children'], options)}*"
    if kind == "delete":
        return f"~{_inlines_to_mrkdwn(node['children'], options)}~"
    if kind == "inlineCode":
        return f"`{escape_mrkdwn(node['value'])}`"
    if kind == "link":
        return f"<{escape_mrkdwn(node['url'])}|{escape_mrkdwn(_node_to_string(node))}>"
    if kind == "image":
        alt = node["alt"] or "Image"
        return f"<{escape_mrkdwn(node['url'])}|{escape_mrkdwn(alt)}>"
    return ""


def _text_to_mrkdwn(text: str, options: Mapping[str, Any]) -> str:
    mentions = _mentions(options)
    users = _named_map(mentions, "users")
    channels = _named_map(mentions, "channels")
    user_groups = _named_map(mentions, "userGroups", "user_groups")
    teams = _named_map(mentions, "teams")

    result: list[str] = []
    last = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > last:
            result.append(escape_mrkdwn(text[last : match.start()]))
        full = match.group(0)
        canonical = _canonical_token(match)
        if canonical is not None:
            result.append(canonical)
        elif match["color"] or match["emoji"]:
            result.append(full)
        elif match["at_name"]:
            name = match["at_name"]
            if name in ("here", "channel", "everyone"):
                result.append(f"<!{name}>")
            elif name in users:
                result.append(f"<@{users[name]}>")
            elif name in user_groups:
                result.append(f"<!subteam^{user_groups[name]}>")
            elif name in teams:
                result.append(f"<!subteam^{teams[name]}>")
            else:
                result.append(escape_mrkdwn(full))
        else:
            name = match["hash_name"]
            if name in channels:
                result.append(f"<#{channels[name]}>")
            else:
                result.append(escape_mrkdwn(full))
        last = match.end()
    if last < len(text):
        result.append(escape_mrkdwn(text[last:]))
    return "".join(result)


def _canonical_token(match: re.Match[str]) -> str | None:
    """Slack's form of an angle token such as ``<@U123|name>``, without the label."""
    if match["broadcast"]:
        return f"<!{match['broadcast']}>"
    if match["user"]:
        return f"<@{match['user']}>"
    if match["channel"]:
        return f"<#{match['channel']}>"
    if match["subteam"]:
        return f"<!subteam^{match['subteam']}>"
    if match["date"]:
        return match.group(0)
    return None
