from markdown_to_slack_blocks import (
    blocks_to_markdown,
    blocks_to_plain_text,
    container_block,
    markdown_to_blocks,
    split_blocks,
)
from markdown_to_slack_blocks.parser import DATA_TABLE_MAX_CELL_CHARACTERS, DATA_TABLE_MAX_ROWS


def _row(cells):
    return [{"type": "raw_text", "text": cell} for cell in cells]


def test_mrkdwn_escapes_text_and_keeps_emitted_tokens():
    text = markdown_to_blocks("a < b & c > d, <foo>")[0]["text"]["text"]
    assert text == "a &lt; b &amp; c &gt; d, &lt;foo&gt;"
    assert markdown_to_blocks(
        "Hello @jdoe and [docs](https://example.com?a=1&b=2) <@U999> <#C123> <!here>",
        {"mentions": {"users": {"jdoe": "U12345"}}},
    )[0]["text"]["text"] == (
        "Hello <@U12345> and <https://example.com?a=1&amp;b=2|docs> <@U999> <#C123> <!here>"
    )
    rich = markdown_to_blocks("a < b", {"preferSectionBlocks": False})
    assert rich[0]["elements"][0]["elements"][0]["text"] == "a < b"
    assert blocks_to_markdown(markdown_to_blocks("a < b & c > d")) == "a < b & c > d"


def test_code_fence_language_survives_a_split():
    blocks = markdown_to_blocks("```python\nline1\nline2\nline3\n```")
    pre = blocks[0]["elements"][0]
    assert pre["language"] == "python"
    assert blocks_to_markdown(blocks).startswith("```python\n")
    split = split_blocks(blocks, {"max_characters": 80})
    languages = [
        element.get("language")
        for batch in split
        for block in batch
        for element in block.get("elements") or []
    ]
    assert languages
    assert set(languages) == {"python"}


def test_long_header_becomes_bold_section():
    short = markdown_to_blocks("# " + ("A" * 150))
    assert short[0]["type"] == "header"
    long = markdown_to_blocks("# " + ("A" * 151) + " < z")
    assert long[0]["type"] == "section"
    assert long[0]["text"]["text"].startswith("*")
    assert '&lt;' in long[0]["text"]["text"]
    rich = markdown_to_blocks("## " + ("B" * 151), {"preferSectionBlocks": False})
    assert rich[0]["type"] == "rich_text"
    assert rich[0]["elements"][0]["elements"][0]["style"] == {"bold": True}
    split = split_blocks(
        [{"type": "header", "text": {"type": "plain_text", "text": "C" * 151}}]
    )[0]
    assert split[0]["type"] == "section"
    assert split[0]["text"]["text"] == "*" + ("C" * 151) + "*"


def test_empty_data_table_cell_and_limits():
    table = markdown_to_blocks("| A | B |\n| --- | --- |\n|  | x |")[0]
    assert table["rows"][1][0] == {"type": "raw_text", "text": " "}

    wide = "| " + " | ".join(f"H{i}" for i in range(21)) + " |\n"
    wide += "| " + " | ".join("---" for _ in range(21)) + " |\n"
    wide += "| " + " | ".join(str(i) for i in range(21)) + " |"
    assert markdown_to_blocks(wide)[0]["type"] == "table"

    body = "\n".join("| h | v |" if False else f"| h | r{i} |" for i in range(DATA_TABLE_MAX_ROWS))
    markdown = "| H | V |\n| --- | --- |\n" + "\n".join(
        f"| h | r{i} |" for i in range(DATA_TABLE_MAX_ROWS)
    )
    blocks = markdown_to_blocks(markdown)
    assert all(block["type"] == "data_table" for block in blocks)
    assert len(blocks) == 2
    assert len(blocks[0]["rows"]) == DATA_TABLE_MAX_ROWS
    assert blocks[0]["rows"][0][0]["text"] == "H"
    assert blocks[1]["rows"][0][0]["text"] == "H"
    assert len(blocks[1]["rows"]) == 2

    huge = "Z" * (DATA_TABLE_MAX_CELL_CHARACTERS - 1)
    split_chars = markdown_to_blocks(
        "| H |\n| --- |\n" + "\n".join(f"| {huge} |" for _ in range(3))
    )
    assert len(split_chars) >= 2
    assert all(block["type"] == "data_table" for block in split_chars)


def test_container_rewrites_data_table_and_plain_text_is_title_only():
    table = markdown_to_blocks("| A | B |\n| --- | --- |\n| 1 | 2 |")[0]
    container = container_block("Sources", [table], collapsible=True)
    assert container["child_blocks"][0]["type"] == "table"
    assert "rows" in container["child_blocks"][0]
    assert blocks_to_plain_text([container]) == "Sources"
    only = blocks_to_plain_text(
        [container_block("First", [{"type": "divider"}]), container_block("Second", [{"type": "divider"}])]
    )
    assert only == "First\n\nSecond"


def test_split_container_caps_children_and_continues_the_title():
    children = [{"type": "divider"} for _ in range(11)]
    container = {
        "type": "container",
        "title": {"type": "plain_text", "text": "Details"},
        "is_collapsible": True,
        "child_blocks": children,
    }
    blocks = split_blocks([container], {"max_characters": 100000})[0]
    assert [block["type"] for block in blocks] == ["container", "container"]
    assert len(blocks[0]["child_blocks"]) == 10
    assert len(blocks[1]["child_blocks"]) == 1
    assert blocks[1]["title"]["text"] == "Details (continued)"
    assert blocks[1]["is_collapsible"] is True

    long_text = "word " * 1200
    packed = split_blocks(
        [
            {
                "type": "container",
                "title": {"type": "plain_text", "text": "Notes"},
                "child_blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": long_text}},
                ],
            }
        ]
    )[0]
    texts = [child["text"]["text"] for child in packed[0]["child_blocks"]]
    assert all(len(text) <= 3000 for text in texts)
    assert "".join(texts).replace(" ", "") == long_text.replace(" ", "")
