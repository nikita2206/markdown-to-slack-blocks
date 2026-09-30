import pytest

from markdown_to_slack_blocks import blocks_to_markdown, markdown_to_blocks

RICH = {"prefer_section_blocks": False}


def mrkdwn(markdown):
    [block] = markdown_to_blocks(markdown)
    assert block["type"] == "section"
    return block["text"]["text"]


def rich_elements(markdown):
    """Top-level rich text elements across all blocks."""
    return [element for block in markdown_to_blocks(markdown, RICH) for element in block["elements"]]


@pytest.mark.parametrize(
    "link",
    [
        "[the PR](https://x.com/pull/1?a=1&b=2)",
        "<https://x.com/pull/1?a=1&b=2|the PR>",
        "<https://x.com/pull/1?a=1&amp;b=2|the PR>",
    ],
)
def test_markdown_and_slack_links_render_the_same(link):
    assert mrkdwn(f"See {link}.") == "See <https://x.com/pull/1?a=1&amp;b=2|the PR>."
    assert markdown_to_blocks(f"- See {link}.") == [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 0,
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "text", "text": "See "},
                                {"type": "link", "url": "https://x.com/pull/1?a=1&b=2", "text": "the PR"},
                                {"type": "text", "text": "."},
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def test_slack_link_url_is_not_parsed_as_markdown():
    assert mrkdwn("<https://x.com/_a_/~b/~c|logs>") == "<https://x.com/_a_/~b/~c|logs>"
    # Without a label the URL is its own text, as for an autolink.
    assert mrkdwn("<https://x.com/a|>") == "<https://x.com/a|https://x.com/a>"


def test_slack_syntax_in_code_and_unsafe_schemes_stays_literal():
    assert mrkdwn("`<https://x.com|label>`") == "`&lt;https://x.com|label&gt;`"
    assert mrkdwn("<javascript:alert(1)|click>") == "&lt;javascript:alert(1)|click&gt;"


def test_labelled_slack_mentions_become_plain_tokens():
    text = "Hi <@U123|nik>, <#C123|general>, <!subteam^S123|@ws>, <!here|here>."
    assert mrkdwn(text) == "Hi <@U123>, <#C123>, <!subteam^S123>, <!here>."
    assert rich_elements(text)[0]["elements"] == [
        {"type": "text", "text": "Hi "},
        {"type": "user", "user_id": "U123"},
        {"type": "text", "text": ", "},
        {"type": "channel", "channel_id": "C123"},
        {"type": "text", "text": ", "},
        {"type": "usergroup", "usergroup_id": "S123"},
        {"type": "text", "text": ", "},
        {"type": "broadcast", "range": "here"},
        {"type": "text", "text": "."},
    ]


def test_bare_urls_with_a_scheme_become_links():
    assert mrkdwn("See https://x.com/a_(b), then https://x.com/c.") == (
        "See <https://x.com/a_(b)|https://x.com/a_(b)>, then <https://x.com/c|https://x.com/c>."
    )
    # A scheme is required: file names and code are left alone.
    assert mrkdwn("Edit settings.py or run `curl https://x.com`.") == "Edit settings.py or run `curl https://x.com`."


@pytest.mark.parametrize("quote", ["", "> "])
def test_slack_link_in_a_table_cell_stays_in_that_cell(quote):
    rows = ["| Log | Note |", "|---|---|", "| <https://x.com|NL logs> | ok |"]
    table, after = markdown_to_blocks("\n".join(quote + row for row in rows) + "\n- next <https://y.com|y>")
    assert table["rows"][1] == [
        {
            "type": "rich_text",
            "elements": [{"type": "rich_text_section", "elements": [{"type": "link", "url": "https://x.com", "text": "NL logs"}]}],
        },
        {"type": "raw_text", "text": "ok"},
    ]
    # A list ends the table without a blank line; its link must not keep the table's escaping.
    assert after["elements"][0]["elements"][0]["elements"][1] == {"type": "link", "url": "https://y.com", "text": "y"}


def test_list_items_keep_code_quotes_and_later_paragraphs():
    markdown = "\n".join(
        [
            "1. Run this:",
            "",
            "   ```bash",
            "   kubectl get pods",
            "   ```",
            "",
            "   Then wait.",
            "2. <!here> check",
            "",
            "   > quoted note",
            "3. ## Heading item",
            "",
            "   second paragraph",
        ]
    )
    assert rich_elements(markdown) == [
        {
            "type": "rich_text_list",
            "style": "ordered",
            "indent": 0,
            "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "Run this:"}]}],
        },
        {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": "kubectl get pods"}], "language": "bash"},
        {"type": "rich_text_section", "elements": [{"type": "text", "text": "Then wait."}]},
        {
            "type": "rich_text_list",
            "style": "ordered",
            "indent": 0,
            "elements": [
                {"type": "rich_text_section", "elements": [{"type": "broadcast", "range": "here"}, {"type": "text", "text": " check"}]}
            ],
            "offset": 1,
        },
        {"type": "rich_text_quote", "elements": [{"type": "text", "text": "quoted note"}]},
        {
            "type": "rich_text_list",
            "style": "ordered",
            "indent": 0,
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "text", "text": "Heading item", "style": {"bold": True}},
                        {"type": "text", "text": "\n"},
                        {"type": "text", "text": "second paragraph"},
                    ],
                }
            ],
            "offset": 2,
        },
    ]


def test_ordered_lists_keep_their_numbers():
    blocks = markdown_to_blocks("3. three\n   - sub\n4. four")
    lists = blocks[0]["elements"]
    assert [(item["style"], item.get("offset")) for item in lists] == [("ordered", 2), ("bullet", None), ("ordered", 3)]
    assert blocks_to_markdown(markdown_to_blocks("3. three\n4. four")) == "3. three\n4. four"


def test_quotes_keep_every_paragraph_list_and_code_block():
    assert rich_elements("> one\n>\n> two\n> - a\n>\n> ```\n> code\n> ```") == [
        {"type": "rich_text_quote", "elements": [{"type": "text", "text": "one"}, {"type": "text", "text": "\n"}, {"type": "text", "text": "two"}]},
        {
            "type": "rich_text_list",
            "style": "bullet",
            "indent": 0,
            "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "a"}]}],
            "border": 1,
        },
        {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": "code"}]},
    ]
