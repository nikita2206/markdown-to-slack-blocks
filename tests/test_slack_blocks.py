import json

from markdown_to_slack_blocks import (
    blocks_to_plain_text,
    markdown_to_blocks,
    slack_blocks_handler,
    slack_blocks_to_text,
    split_blocks,
)

OPTS = {"xml_tag_handlers": {"slack-blocks": slack_blocks_handler}}

MESSAGE = """Before 2 < 5.

<slack-blocks>
{"text": "Deployed **api** to prod. Load stayed under 5.", "blocks": [
  {"type": "section", "text": {"type": "mrkdwn", "text": "a < b & c <https://example.com?a=1&b=2|logs>"}},
  {"type": "actions", "block_id": "roll", "elements": [
    {"type": "button", "action_id": "rollback", "text": {"type": "plain_text", "text": "Rollback"}}
  ]}
]}
</slack-blocks>

After & done.
"""


def test_json_blocks_pass_through_unchanged():
    blocks = markdown_to_blocks(MESSAGE, OPTS)
    assert [block["type"] for block in blocks] == ["section", "section", "actions", "section"]
    assert blocks[0]["text"]["text"] == "Before 2 &lt; 5."
    assert blocks[1]["text"]["text"] == "a < b & c <https://example.com?a=1&b=2|logs>"
    assert blocks[2]["block_id"] == "roll"
    assert blocks[2]["elements"][0]["text"]["text"] == "Rollback"
    assert blocks[3]["text"]["text"] == "After &amp; done."
    # The web fallback is not inserted as an extra Slack block.
    assert all("Deployed" not in json.dumps(block) for block in blocks)
    assert split_blocks(blocks) == [blocks]


def test_web_ui_uses_text_fallback_not_raw_json():
    web = slack_blocks_to_text(MESSAGE)
    assert "Deployed **api** to prod. Load stayed under 5." in web
    assert "Before 2 < 5." in web
    assert "After & done." in web
    assert "<slack-blocks>" not in web
    assert "action_id" not in web
    assert "example.com" not in web
    plain = blocks_to_plain_text(markdown_to_blocks(MESSAGE, OPTS))
    assert "Rollback" not in plain
    assert "a < b & c" in plain


def test_fenced_json_and_single_block():
    fenced = """
<slack-blocks>
```json
[{"type": "header", "text": {"type": "plain_text", "text": "Deploy"}}]
```
</slack-blocks>
"""
    blocks = markdown_to_blocks(fenced, OPTS)
    assert blocks == [{"type": "header", "text": {"type": "plain_text", "text": "Deploy"}}]
    web = slack_blocks_to_text(fenced)
    assert web.strip() == "# Deploy"
    assert "```" not in web

    same_line = '<slack-blocks>{"type": "divider"}</slack-blocks>'
    assert markdown_to_blocks(same_line, OPTS) == [{"type": "divider"}]


def test_invalid_json_falls_back_to_markdown():
    source = "<slack-blocks>\nnot json, just **bold** and 2 < 5\n</slack-blocks>\n"
    blocks = markdown_to_blocks(source, OPTS)
    assert blocks == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "not json, just *bold* and 2 &lt; 5"}}
    ]
    web = slack_blocks_to_text(source)
    assert "<slack-blocks>" not in web
    assert "not json, just **bold** and 2 < 5" in web


def test_text_only_object_is_markdown_on_both_paths():
    source = """<slack-blocks>
{"text": "Just a note with 2 < 5."}
</slack-blocks>
"""
    blocks = markdown_to_blocks(source, OPTS)
    assert blocks[0]["text"]["text"] == "Just a note with 2 &lt; 5."
    assert "Just a note with 2 < 5." in slack_blocks_to_text(source)


def test_tag_inside_a_fence_is_left_alone():
    source = "```\n<slack-blocks>\n[]\n</slack-blocks>\n```\n"
    blocks = markdown_to_blocks(source, OPTS)
    assert blocks[0]["type"] == "rich_text"
    code = blocks[0]["elements"][0]
    assert code["type"] == "rich_text_preformatted"
    assert "<slack-blocks>" in code["elements"][0]["text"]
    assert "<slack-blocks>" in slack_blocks_to_text(source)
