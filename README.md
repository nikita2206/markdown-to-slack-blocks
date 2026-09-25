# markdown-to-slack-blocks

Convert Markdown into Slack [Block Kit](https://api.slack.com/block-kit) JSON, and render blocks back to Markdown or plain text.

This is a Python port of [udivankin/markdown-to-slack-blocks](https://github.com/udivankin/markdown-to-slack-blocks) v1.6.1 (MIT), released here as 1.1.0. It is aimed at the same job: take Markdown from people or from an LLM and post it to Slack without losing headings, lists, code, tables, or mentions.

```bash
pip install markdown-to-slack-blocks
```

```python
from markdown_to_slack_blocks import markdown_to_blocks

blocks = markdown_to_blocks("""
# Hello World
This is a **bold** statement.
""")
```

`markdown_to_blocks` is also available as `markdownToBlocks` if you are moving a call site over from the JavaScript package. The same aliases exist for `splitBlocks`, `splitBlocksWithText`, `blocksToMarkdown`, and `blocksToPlainText`.

## What it emits

| Markdown | Block |
| --- | --- |
| Paragraphs | `section` (`mrkdwn`) by default, or `rich_text` |
| `#` / `##` | `header` when the plain text is at most 150 characters, otherwise the bold form used for `###` |
| `###` and below | bold `section`, or a bold `rich_text` section |
| Lists, quotes, fenced code | `rich_text` (`rich_text_list`, `rich_text_quote`, `rich_text_preformatted`). A fence info string such as `python` is copied to `language` |
| `---` | `divider` |
| A paragraph that is only an image | `image` |
| GFM tables | `data_table` (or legacy `table`) |

Inline styles become Slack mrkdwn (`*bold*`, `_italic_`, `~strike~`, `` `code` ``) inside sections, and `rich_text` style objects otherwise. Links become `<url|label>`.

Section mrkdwn escapes `&`, `<`, and `>` as `&amp;`, `&lt;`, and `&gt;`, except for tokens this library emits (`<url|label>`, `<@U…>`, `<#C…>`, `<!…>`). `rich_text` text is left as written, because Slack does not parse mrkdwn there. A `header` block is plain text and is not escaped. A heading longer than 150 characters is emitted as a bold section instead, and that text is escaped.

Slack-specific tokens are recognized in the text:

- `<@U…>`, `<#C…>`, `<!subteam^S…>`, `<!subteam^T…>`
- `<!here>`, `<!channel>`, `<!everyone>`
- `<!date^timestamp^format|fallback>`
- `:emoji:` shortcodes
- `#rrggbb` color swatches when color detection is on

## Options

```python
blocks = markdown_to_blocks(markdown, {
    "mentions": {
        "users": {"username": "U123456"},
        "channels": {"general": "C123456"},
        "user_groups": {"engineers": "S123456"},  # or "userGroups"
        "teams": {"myteam": "T123456"},
    },
    "detect_colors": True,            # detectColors
    "prefer_section_blocks": True,    # preferSectionBlocks, default True
    "table_block_type": "data_table", # "table" for the legacy block
    "table_caption": "Data table",    # "" omits the caption
})
```

Mention IDs are checked before conversion:

- users start with `U` or `W`
- channels start with `C`
- user groups start with `S`
- teams start with `T`

and the rest of the ID is uppercase alphanumeric.

### XML tag handlers

Tags the library does not know, such as `<sources>` or `<detailed>`, are not Slack blocks. Pass `xml_tag_handlers` (`xmlTagHandlers`) to turn specific elements into whatever blocks you want. The handler is called with an `XmlTagContext`: the element name, its attributes, and the inner Markdown. `convert` parses that inner Markdown with the same options, so nested elements work too.

The tags are parsed with Python's [expat](https://docs.python.org/3/library/pyexpat.html) XML parser, not a regular expression. Names are case-sensitive. Attributes follow XML rules: values are quoted, and entities such as `&amp;` are decoded. The text inside the element is Markdown, so it is not parsed as XML. `a < b` and a raw `&` in the body are kept as written. Tags inside fenced code are left alone.

Registered names are lenient, because registering a handler means you expect that tag. A matching close tag still wins, so nesting works. If the start tag never closes, the body runs until the next registered start tag, or to the end of the input, and it does not swallow a later element. A stray close tag for a registered name is dropped. Unregistered tags, and comparisons such as `2 < 5`, stay as Markdown.

Slack's [`container`](https://docs.slack.dev/reference/block-kit/blocks/container-block/) block is the usual wrapper. `container_block` builds one. Slack allows at most 10 children and a plain-text title of at most 150 characters; `split_blocks` enforces both. A `data_table` is not a legal container child, so `container_block` rewrites those children to the legacy `table` block.

```python
from markdown_to_slack_blocks import container_block, markdown_to_blocks

def sources(tag):
    children = tag.convert(tag.body)
    if not children:
        return []
    return container_block(tag.attrs.get("title") or "Sources", children, collapsible=True)

def detailed(tag):
    return container_block(
        "Details",
        tag.convert(tag.body),
        collapsible=True,
        default_collapsed=True,
    )

blocks = markdown_to_blocks(agent_markdown, {
    "xml_tag_handlers": {"sources": sources, "detailed": detailed},
})
```

```xml
Answer text.

<sources title="References">
- [Runbook](https://example.com/runbook)
</sources>

<detailed>
## Investigation
The check failed because **disk** was full.
</detailed>
```

Return one block, a list of blocks, or an empty list to drop the element. Return `None` to leave that occurrence as normal Markdown.

`register_xml_tag_handler("sources", sources)` installs a process-wide default. An `xml_tag_handlers` entry overrides it, and setting the name to `None` there turns the global handler off for that call. `clear_xml_tag_handlers()` removes the defaults.

### Tables

Cells are typed from their content:

| Cell | Slack cell |
| --- | --- |
| Plain text | `raw_text` |
| A plain number (`10`, `-3.5`) | `raw_number` |
| Styles, links, mentions, emoji | `rich_text` |

An empty cell is a single space. Slack rejects `raw_text` whose text is empty.

A `data_table` must have at least 2 rows including the header, at most 20 columns, at most 201 rows, and at most 20,000 characters of cell text. More than 20 columns, or fewer than 2 rows, becomes one legacy `table`. Extra rows, or a character count past 20,000, are split into further `data_table` blocks that repeat the header. One header plus one row that is already over 20,000 characters becomes a legacy `table` by itself.

### Large messages

Slack rejects messages that are too big. `split_blocks` cuts on block boundaries, then inside `rich_text`, then by line inside code blocks. The fence `language` is kept on each piece. Section text is chunked at 3,000 characters. A header longer than 150 characters is rewritten as bold sections (the same form as a `###` heading) instead of being split as a `header` block.

A container that has more than 10 children, or whose JSON is over the size limit, becomes several containers with the same settings. Later pieces use the title `{title} (continued)`, truncated so a plain-text title stays within 150 characters. Children are normalised first: sections and headers are chunked, nested containers are split, and any `data_table` is rewritten to `table`.

```python
from markdown_to_slack_blocks import markdown_to_blocks, split_blocks_with_text

for batch in split_blocks_with_text(markdown_to_blocks(very_long_markdown)):
    client.chat_postMessage(channel=channel, text=batch["text"], blocks=batch["blocks"])
```

Limits default to 40 blocks and 12,000 JSON characters (`max_blocks` / `maxBlocks`, `max_characters` / `maxCharacters`).

### Back to Markdown or plain text

```python
from markdown_to_slack_blocks import blocks_to_markdown, blocks_to_plain_text

text = blocks_to_plain_text(blocks)  # chat.postMessage fallback; a container contributes its title only
markdown = blocks_to_markdown(blocks, {
    "mentions": {
        "users": {"U123456": "username"},
        "channels": {"C123456": "general"},
        "user_groups": {"S123456": "engineers"},
        "teams": {"T123456": "myteam"},
    }
})
```

The Markdown is canonical rather than byte-for-byte identical to the source. Blocks this library produced round-trip cleanly.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests include the upstream fixture corpus (`tests/fixtures`) and check both directions against it.

## License

MIT. The original library is copyright https://github.com/udivankin. This Python port is copyright Nikita Nefedov. See [LICENSE](LICENSE).
