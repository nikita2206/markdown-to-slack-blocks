import json
from pathlib import Path

import pytest

from markdown_to_slack_blocks import (
    blocks_to_markdown,
    blocks_to_plain_text,
    markdown_to_blocks,
    split_blocks,
    split_blocks_with_text,
    validate_blocks_to_markdown_options,
    validate_options,
)

FIXTURES = Path(__file__).parent / "fixtures"
MENTIONS = json.loads((FIXTURES / "mentions.json").read_text())
REVERSED = json.loads((FIXTURES / "mentions.reversed.json").read_text())


def options(prefer_section_blocks=None):
    result = {"mentions": MENTIONS, "detectColors": True}
    if prefer_section_blocks is not None:
        result["preferSectionBlocks"] = prefer_section_blocks
    return result


def test_plain_text_section_by_default():
    assert markdown_to_blocks("Hello world") == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Hello world"}}
    ]


def test_plain_text_rich_text_when_sections_disabled():
    result = markdown_to_blocks("Hello world", {"preferSectionBlocks": False})
    assert result == [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
    ]
    assert "style" not in result[0]["elements"][0]["elements"][0]


def test_headers():
    result = markdown_to_blocks("# Heading 1\n## Heading 2")
    assert result == [
        {"type": "header", "text": {"type": "plain_text", "text": "Heading 1"}},
        {"type": "header", "text": {"type": "plain_text", "text": "Heading 2"}},
    ]


def test_bold_italic_mrkdwn():
    result = markdown_to_blocks("This is *italic* and **bold**")
    assert result[0]["text"]["text"] == "This is _italic_ and *bold*"


def test_bold_italic_rich_text():
    result = markdown_to_blocks(
        "This is *italic* and **bold**", {"preferSectionBlocks": False}
    )
    assert result[0]["elements"][0]["elements"] == [
        {"type": "text", "text": "This is "},
        {"type": "text", "text": "italic", "style": {"italic": True}},
        {"type": "text", "text": " and "},
        {"type": "text", "text": "bold", "style": {"bold": True}},
    ]


def test_lists_code_quote_table():
    bullets = markdown_to_blocks("- Item 1\n- Item 2")
    assert bullets[0]["elements"][0]["style"] == "bullet"
    ordered = markdown_to_blocks("1. Item 1\n2. Item 2")
    assert ordered[0]["elements"][0]["style"] == "ordered"
    code = markdown_to_blocks("```\nconst x = 1;\n```")
    assert code[0]["elements"][0]["elements"][0]["text"] == "const x = 1;"
    quote = markdown_to_blocks("> This is a quote")
    assert quote[0]["elements"][0]["type"] == "rich_text_quote"
    table = markdown_to_blocks("| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |")
    assert table[0]["type"] == "data_table"
    assert table[0]["caption"] == "Data table"
    assert table[0]["rows"][1][0] == {"type": "raw_text", "text": "Cell 1"}


def test_dynamic_table_cells_and_legacy():
    markdown = (
        "| Name | Amount | Stage |\n| --- | --- | --- |\n"
        "| Alpha | 10 | **Negotiation** |\n| Bravo | 2.5 | Waiting on *review* |"
    )
    rows = markdown_to_blocks(markdown)[0]["rows"]
    assert rows[1][1] == {"type": "raw_number", "value": 10, "text": "10"}
    assert rows[2][1]["value"] == 2.5
    assert rows[1][2]["type"] == "rich_text"
    money = markdown_to_blocks(
        "| Plain | Money | Mixed |\n| --- | --- | --- |\n| text | $1.2M | 10x |"
    )
    assert [cell["type"] for cell in money[0]["rows"][1]] == [
        "raw_text",
        "raw_text",
        "raw_text",
    ]
    legacy = markdown_to_blocks(
        "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | 10 |",
        {"tableBlockType": "table"},
    )
    assert legacy[0]["type"] == "table"
    assert "caption" not in markdown_to_blocks(
        "| A | B |\n| --- | --- |\n| 1 | 2 |", {"tableCaption": ""}
    )[0]
    assert markdown_to_blocks("| A | B |\n| --- | --- |\n| 1 | 2 |", {"tableCaption": "Deals"})[
        0
    ]["caption"] == "Deals"


def test_mentions_colors_dates_emoji():
    opts = {
        "mentions": {
            "users": {"jdoe": "U12345", "sally": "U67890"},
            "channels": {"general": "C00001"},
            "userGroups": {"devs": "S99999"},
        },
        "detectColors": True,
        "preferSectionBlocks": False,
    }
    users = markdown_to_blocks("Hello @jdoe and @sally", opts)[0]["elements"][0]["elements"]
    assert users[1] == {"type": "user", "user_id": "U12345"}
    assert users[3] == {"type": "user", "user_id": "U67890"}
    unknown = markdown_to_blocks("Hello @unknown", opts)[0]["elements"][0]["elements"]
    assert unknown[1] == {"type": "text", "text": "@unknown"}
    channel = markdown_to_blocks("Join #general", opts)[0]["elements"][0]["elements"]
    assert channel[1] == {"type": "channel", "channel_id": "C00001"}
    group = markdown_to_blocks("cc @devs", opts)[0]["elements"][0]["elements"]
    assert group[1] == {"type": "usergroup", "usergroup_id": "S99999"}
    color = markdown_to_blocks("Color #ff0000 is red", opts)[0]["elements"][0]["elements"]
    assert color[1] == {"type": "color", "value": "#ff0000"}
    mixed = markdown_to_blocks("**@jdoe** check #general", opts)[0]["elements"][0]["elements"]
    assert mixed[0] == {"type": "user", "user_id": "U12345", "style": {"bold": True}}

    rich = {"preferSectionBlocks": False}
    assert markdown_to_blocks("<@U123456>", rich)[0]["elements"][0]["elements"] == [
        {"type": "user", "user_id": "U123456"}
    ]
    assert markdown_to_blocks("<!subteam^T123456>", rich)[0]["elements"][0]["elements"] == [
        {"type": "team", "team_id": "T123456"}
    ]
    assert markdown_to_blocks("<!subteam^S123456>", rich)[0]["elements"][0]["elements"] == [
        {"type": "usergroup", "usergroup_id": "S123456"}
    ]
    assert markdown_to_blocks("<#C123456>", rich)[0]["elements"][0]["elements"] == [
        {"type": "channel", "channel_id": "C123456"}
    ]
    broadcasts = markdown_to_blocks("<!here> <!channel> <!everyone>", rich)[0]["elements"][0][
        "elements"
    ]
    assert [item["type"] for item in broadcasts] == [
        "broadcast",
        "text",
        "broadcast",
        "text",
        "broadcast",
    ]
    date = markdown_to_blocks("<!date^1620000000^{date_short}|fallback>", rich)
    assert date[0]["elements"][0]["elements"][0] == {
        "type": "date",
        "timestamp": 1620000000,
        "format": "{date_short}",
    }
    assert markdown_to_blocks(":smile:", rich)[0]["elements"][0]["elements"] == [
        {"type": "emoji", "name": "smile"}
    ]
    code = markdown_to_blocks("`<@U123>`", rich)[0]["elements"][0]["elements"][0]
    assert code == {"type": "text", "text": "<@U123>", "style": {"code": True}}


def test_section_mentions():
    assert markdown_to_blocks(
        "Hello @jdoe!", {"mentions": {"users": {"jdoe": "U12345"}}}
    )[0]["text"]["text"] == "Hello <@U12345>!"
    assert markdown_to_blocks("Hello @unknown!")[0]["text"]["text"] == "Hello @unknown!"
    assert (
        markdown_to_blocks("Check out #general", {"mentions": {"channels": {"general": "C12345"}}})[
            0
        ]["text"]["text"]
        == "Check out <#C12345>"
    )
    assert markdown_to_blocks("@here @channel @everyone")[0]["text"]["text"] == (
        "<!here> <!channel> <!everyone>"
    )
    assert markdown_to_blocks("Hello <@U12345> and <#C67890>")[0]["text"]["text"] == (
        "Hello <@U12345> and <#C67890>"
    )
    assert markdown_to_blocks("Hello :wave: there")[0]["text"]["text"] == "Hello :wave: there"
    assert markdown_to_blocks("Check [this link](https://example.com)")[0]["text"]["text"] == (
        "Check <https://example.com|this link>"
    )


def test_format_wrapped_lists():
    bold = markdown_to_blocks("**1. Bold item**", {"preferSectionBlocks": False})
    assert bold[0]["elements"][0]["elements"][0]["elements"][0]["text"] == "Bold item"
    assert bold[0]["elements"][0]["style"] == "ordered"
    italic = markdown_to_blocks("*1. Italic item*", {"preferSectionBlocks": False})
    assert italic[0]["elements"][0]["elements"][0]["elements"][0]["style"] == {"italic": True}
    strike = markdown_to_blocks("~1. Strike item~", {"preferSectionBlocks": False})
    assert strike[0]["elements"][0]["elements"][0]["elements"][0]["style"] == {"strike": True}
    nested = markdown_to_blocks("- item 1\n  **- item 2**", {"preferSectionBlocks": False})
    assert nested[0]["elements"][1]["indent"] == 1
    heading = markdown_to_blocks("### **1. Bold heading list**")
    assert heading[0]["type"] == "rich_text"
    assert heading[0]["elements"][0]["style"] == "ordered"


def test_nested_lists():
    result = markdown_to_blocks("- Level 1\n  - Level 2 with **bold**\n    - Level 3")
    indents = [element["indent"] for element in result[0]["elements"]]
    assert indents == [0, 1, 2]


def test_rich_text_fixture():
    markdown = (FIXTURES / "input.md").read_text()
    expected = json.loads((FIXTURES / "output_rich_text.json").read_text())
    assert markdown_to_blocks(markdown, options(False)) == expected


def test_sections_fixture():
    markdown = (FIXTURES / "input.md").read_text()
    expected = json.loads((FIXTURES / "output_sections.json").read_text())
    assert markdown_to_blocks(markdown, options(True)) == expected


def test_sections_markdown_roundtrip_text():
    markdown = (FIXTURES / "input.md").read_text()
    expected = (FIXTURES / "output_sections.md").read_text().rstrip("\n")
    blocks = markdown_to_blocks(markdown, options(True))
    assert blocks_to_markdown(blocks, {"mentions": REVERSED}) == expected


def test_long_fixture_and_split():
    markdown = (FIXTURES / "input_long.md").read_text()
    expected_blocks = json.loads((FIXTURES / "output_long.json").read_text())
    expected_batches = json.loads((FIXTURES / "output_long_split.json").read_text())
    blocks = markdown_to_blocks(markdown)
    assert blocks == expected_blocks
    result = split_blocks_with_text(blocks)
    assert result == expected_batches


def test_large_split_respects_limits():
    markdown = (FIXTURES / "input.md").read_text()
    large = ("\n\n---\n\n").join([markdown] * 10)
    blocks = markdown_to_blocks(large, options())
    batches = split_blocks(blocks)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == len(blocks)
    for batch in batches:
        assert len(batch) <= 40
        assert len(json.dumps(batch, ensure_ascii=False, separators=(",", ":"))) <= 12000


def test_markdown_renderer_examples():
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Title"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Hello *bold*, _italic_, ~strike~, `code`, <https://example.com|link>, <@U12345>, <!here>",
            },
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 0,
                    "elements": [
                        {"type": "rich_text_section", "elements": [{"type": "text", "text": "First item"}]}
                    ],
                },
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 1,
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "Nested item"}]
                        }
                    ],
                },
            ],
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_quote",
                    "elements": [{"type": "text", "text": "Quoted text"}],
                }
            ],
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": "const x = 1;"}],
                }
            ],
        },
    ]
    assert blocks_to_markdown(blocks) == "\n".join(
        [
            "# Title",
            "",
            "Hello **bold**, *italic*, ~strike~, `code`, [link](<https://example.com>), <@U12345>, <!here>",
            "",
            "- First item",
            "  - Nested item",
            "",
            "> Quoted text",
            "",
            "```",
            "const x = 1;",
            "```",
        ]
    )


def test_reverse_mentions():
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Hello <@U12345>, join <#C00001>, ask <!subteam^S12345>, notify <!subteam^T123456>, format <@S12345>, ping <@here>",
            },
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "user", "user_id": "U12345"},
                        {"type": "text", "text": " in "},
                        {"type": "channel", "channel_id": "C00001"},
                        {"type": "text", "text": " with "},
                        {"type": "usergroup", "usergroup_id": "S12345"},
                        {"type": "text", "text": " and "},
                        {"type": "team", "team_id": "T123456"},
                    ],
                }
            ],
        },
    ]
    assert blocks_to_markdown(blocks, {"mentions": REVERSED}) == "\n".join(
        [
            "Hello @jdoe, join #general, ask @devs, notify @T123456, format @devs, ping <@here>",
            "",
            "@jdoe in #general with @devs and @T123456",
        ]
    )


def test_table_markdown_roundtrip():
    markdown = (
        "| Name | Amount | Stage |\n| --- | --- | --- |\n"
        "| Alpha | 10 | **Won** |\n| Bravo | 2.5 | Waiting on *review* |"
    )
    blocks = markdown_to_blocks(markdown)
    assert markdown_to_blocks(blocks_to_markdown(blocks)) == blocks


def test_fixture_block_roundtrips():
    rich = json.loads((FIXTURES / "output_rich_text.json").read_text())
    assert markdown_to_blocks(
        blocks_to_markdown(rich),
        {"detectColors": True, "preferSectionBlocks": False},
    ) == rich
    sections = json.loads((FIXTURES / "output_sections.json").read_text())
    assert markdown_to_blocks(
        blocks_to_markdown(sections),
        {"detectColors": True, "preferSectionBlocks": True},
    ) == sections


def test_split_block_counts_and_text():
    def rich(text):
        return {
            "type": "rich_text",
            "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}],
        }

    assert split_blocks([]) == [[]]
    blocks = [rich(f"Block {i}") for i in range(50)]
    batches = split_blocks(blocks)
    assert all(len(batch) <= 40 for batch in batches)
    assert sum(len(batch) for batch in batches) == 50
    custom = split_blocks([rich(f"Block {i}") for i in range(15)], {"maxBlocks": 5})
    assert [len(batch) for batch in custom] == [5, 5, 5]

    long_text = "x" * 3000
    char_batches = split_blocks([rich(long_text) for _ in range(10)])
    assert len(char_batches) > 1
    for batch in char_batches:
        assert len(json.dumps(batch, ensure_ascii=False, separators=(",", ":"))) <= 12000

    section = {"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3000 + "y" * 500}}
    split_section = split_blocks([section])
    assert len(split_section[0]) == 2
    assert len(split_section[0][0]["text"]["text"]) == 3000
    assert split_section[0][1]["text"]["text"].startswith("y")

    part1 = "a" * 2900
    part2 = "b" * 200
    newline_split = split_blocks(
        [{"type": "section", "text": {"type": "mrkdwn", "text": f"{part1}\n{part2}"}}]
    )
    assert newline_split[0][0]["text"]["text"] == part1
    assert newline_split[0][1]["text"]["text"] == part2

    header = split_blocks([{"type": "header", "text": {"type": "plain_text", "text": "H" * 3500}}])
    assert all(block["type"] == "section" for block in header[0])
    assert all(len(block["text"]["text"]) <= 3000 for block in header[0])
    assert "".join(block["text"]["text"].replace("*", "") for block in header[0]) == "H" * 3500

    with_text = split_blocks_with_text(
        [
            {"type": "header", "text": {"type": "plain_text", "text": "Title"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "Hello *world*"}},
            {"type": "divider"},
            rich("Final line"),
        ]
    )
    assert "Title" in with_text[0]["text"]
    assert "---" in with_text[0]["text"]
    assert blocks_to_plain_text(
        [
            {
                "type": "data_table",
                "rows": [
                    [{"type": "raw_text", "text": "Name"}, {"type": "raw_text", "text": "Amount"}],
                    [{"type": "raw_text", "text": "Alpha"}, {"type": "raw_number", "value": 10, "text": "10"}],
                ],
            }
        ]
    ).splitlines() == ["Name | Amount", "Alpha | 10"]


def test_validation():
    validate_options(
        {
            "mentions": {
                "users": {"u1": "U12345", "u2": "W12345"},
                "channels": {"c1": "C12345"},
                "user_groups": {"g1": "S12345"},
                "teams": {"t1": "T12345"},
            }
        }
    )
    validate_options(None)
    validate_options({"mentions": {}})
    with pytest.raises(ValueError, match="Invalid User ID"):
        validate_options({"mentions": {"users": {"bad": "X12345"}}})
    with pytest.raises(ValueError, match="alphanumeric"):
        validate_options({"mentions": {"users": {"bad": "U123-45"}}})
    with pytest.raises(ValueError, match="Invalid Channel ID"):
        validate_options({"mentions": {"channels": {"bad": "U12345"}}})
    with pytest.raises(ValueError, match="Invalid User Group ID"):
        validate_options({"mentions": {"userGroups": {"bad": "G12345"}}})
    with pytest.raises(ValueError, match="Invalid Team ID"):
        validate_options({"mentions": {"teams": {"bad": "S12345"}}})
    validate_blocks_to_markdown_options(
        {
            "mentions": {
                "users": {"U12345": "jdoe", "W12345": "sally"},
                "channels": {"C12345": "general"},
                "userGroups": {"S12345": "devs"},
                "teams": {"T12345": "team"},
            }
        }
    )
    with pytest.raises(ValueError, match="Invalid Channel ID key"):
        validate_blocks_to_markdown_options({"mentions": {"channels": {"U12345": "general"}}})
