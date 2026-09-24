# markdown-to-slack-blocks

Convert Markdown into Slack [Block Kit](https://api.slack.com/block-kit) JSON, and render blocks back to Markdown or plain text.

This is a Python port of [udivankin/markdown-to-slack-blocks](https://github.com/udivankin/markdown-to-slack-blocks) v1.6.1 (MIT), released here as 1.0.0. It is aimed at the same job: take Markdown from people or from an LLM and post it to Slack without losing headings, lists, code, tables, or mentions.

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
| `#` / `##` | `header` |
| `###` and below | bold `section`, or a bold `rich_text` section |
| Lists, quotes, fenced code | `rich_text` (`rich_text_list`, `rich_text_quote`, `rich_text_preformatted`) |
| `---` | `divider` |
| A paragraph that is only an image | `image` |
| GFM tables | `data_table` (or legacy `table`) |

Inline styles become Slack mrkdwn (`*bold*`, `_italic_`, `~strike~`, `` `code` ``) inside sections, and `rich_text` style objects otherwise. Links become `<url|label>`.

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

### Tables

Cells are typed from their content:

| Cell | Slack cell |
| --- | --- |
| Plain text | `raw_text` |
| A plain number (`10`, `-3.5`) | `raw_number` |
| Styles, links, mentions, emoji | `rich_text` |

### Large messages

Slack rejects messages that are too big. `split_blocks` cuts on block boundaries, then inside `rich_text`, then by line inside code blocks. Section and header text is chunked at 3,000 characters first.

```python
from markdown_to_slack_blocks import markdown_to_blocks, split_blocks_with_text

for batch in split_blocks_with_text(markdown_to_blocks(very_long_markdown)):
    client.chat_postMessage(channel=channel, text=batch["text"], blocks=batch["blocks"])
```

Limits default to 40 blocks and 12,000 JSON characters (`max_blocks` / `maxBlocks`, `max_characters` / `maxCharacters`).

### Back to Markdown or plain text

```python
from markdown_to_slack_blocks import blocks_to_markdown, blocks_to_plain_text

text = blocks_to_plain_text(blocks)  # chat.postMessage fallback
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
