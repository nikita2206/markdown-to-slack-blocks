from markdown_to_slack_blocks import (
    clear_xml_tag_handlers,
    container_block,
    markdown_to_blocks,
    register_xml_tag_handler,
)


def _sources(tag):
    return container_block(
        tag.attrs.get("title") or "Sources",
        tag.convert(tag.body),
        collapsible=tag.attrs.get("collapsible") == "true",
    )


def _detailed(tag):
    children = tag.convert(tag.body)
    if not children:
        return []
    return container_block(
        "Details",
        children,
        collapsible=True,
        default_collapsed=True,
    )


HANDLERS = {"xml_tag_handlers": {"sources": _sources, "detailed": _detailed}}


def _block_texts(blocks):
    texts = []
    for block in blocks:
        if block["type"] == "section":
            texts.append(block["text"]["text"])
        elif block["type"] == "rich_text":
            for element in block["elements"]:
                for child in element.get("elements") or []:
                    if child.get("type") == "text":
                        texts.append(child["text"])
    return texts


def test_without_handlers_tags_stay_text():
    blocks = markdown_to_blocks("Before\n\n<sources>\n- a\n</sources>")
    assert all(block["type"] != "container" for block in blocks)
    blob = str(blocks)
    assert "sources" in blob


def test_block_tag_becomes_container_and_keeps_neighbors():
    markdown = """Answer text.

<sources title="References">
- [Runbook](https://example.com/runbook)
</sources>

After.
"""
    blocks = markdown_to_blocks(markdown, {"xml_tag_handlers": {"sources": _sources}})
    assert [block["type"] for block in blocks] == ["section", "container", "section"]
    container = blocks[1]
    assert container["title"] == {"type": "plain_text", "text": "References"}
    assert container["is_collapsible"] is False
    assert container["child_blocks"][0]["type"] == "rich_text"
    item = container["child_blocks"][0]["elements"][0]["elements"][0]["elements"]
    assert item[0]["type"] == "link"
    assert item[0]["url"] == "https://example.com/runbook"
    assert blocks[0]["text"]["text"] == "Answer text."
    assert blocks[2]["text"]["text"] == "After."


def test_detailed_collapses_and_renders_inner_markdown():
    markdown = "<detailed>\n## Investigation\nThe check failed because **disk** was full.\n</detailed>"
    blocks = markdown_to_blocks(markdown, {"xml_tag_handlers": {"detailed": _detailed}})
    assert blocks[0]["type"] == "container"
    assert blocks[0]["default_collapsed"] is True
    assert blocks[0]["is_collapsible"] is True
    children = blocks[0]["child_blocks"]
    assert children[0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "Investigation"},
    }
    assert children[1]["text"]["text"] == "The check failed because *disk* was full."


def test_nested_tags_and_xml_entities():
    amp = "&" + "amp;"
    lt = "&" + "lt;"
    markdown = (
        '<detailed collapsible="true">\n'
        f'<sources title="Refs {amp} notes">\n'
        f"a {lt} b {amp} c\n"
        "</sources>\n"
        "</detailed>"
    )
    blocks = markdown_to_blocks(markdown, HANDLERS)
    assert len(blocks) == 1
    inner = blocks[0]["child_blocks"]
    assert inner[0]["type"] == "container"
    assert inner[0]["title"]["text"] == "Refs & notes"
    assert inner[0]["child_blocks"][0]["text"]["text"] == "a &lt; b &amp; c"


def test_markdown_body_is_not_parsed_as_xml():
    markdown = "<sources>\na < b & c\n</sources>"
    blocks = markdown_to_blocks(markdown, {"xml_tag_handlers": {"sources": _sources}})
    assert blocks[0]["child_blocks"][0]["text"]["text"] == "a &lt; b &amp; c"


def test_ill_formed_tag_is_left_alone():
    blocks = markdown_to_blocks(
        '<sources title="x" collapsible>\nHi\n</sources>',
        {"xml_tag_handlers": {"sources": _sources}},
    )
    assert all(block["type"] != "container" for block in blocks)


def test_tag_inside_fence_is_not_intercepted():
    markdown = "```\n<sources>\nsecret\n</sources>\n```"
    blocks = markdown_to_blocks(markdown, {"xml_tag_handlers": {"sources": _sources}})
    assert blocks[0]["type"] == "rich_text"
    assert "secret" in blocks[0]["elements"][0]["elements"][0]["text"]
    assert all(block["type"] != "container" for block in blocks)


def test_self_closing_and_drop_empty():
    seen = {}

    def sources(tag):
        seen["body"] = tag.body
        seen["attrs"] = dict(tag.attrs)
        return _sources(tag)

    blocks = markdown_to_blocks(
        '<sources title="Empty" />',
        {"xml_tag_handlers": {"sources": sources, "detailed": _detailed}},
    )
    assert seen["body"] == ""
    assert seen["attrs"] == {"title": "Empty"}
    assert blocks == [container_block("Empty", [], collapsible=False)]

    assert (
        markdown_to_blocks("<detailed>\n\n</detailed>", {"xml_tag_handlers": {"detailed": _detailed}})
        == []
    )


def test_none_falls_through_and_rich_text_mode_splits():
    def ignore(_tag):
        return None

    raw = markdown_to_blocks("<sources>hello</sources>")
    skipped = markdown_to_blocks(
        "<sources>hello</sources>",
        {"xml_tag_handlers": {"sources": ignore}},
    )
    assert skipped == raw

    def box(tag):
        return container_block("Box", tag.convert(tag.body))

    blocks = markdown_to_blocks(
        "Before\n\n<sources>\nitem\n</sources>\n\nAfter",
        {"xml_tag_handlers": {"sources": box}, "preferSectionBlocks": False},
    )
    assert [block["type"] for block in blocks] == ["rich_text", "container", "rich_text"]
    assert blocks[1]["child_blocks"][0]["type"] == "rich_text"


def test_process_wide_handler_can_be_overridden():
    register_xml_tag_handler("sources", _sources)
    try:
        blocks = markdown_to_blocks("<sources>\nHi\n</sources>")
        assert blocks[0]["type"] == "container"
        disabled = markdown_to_blocks(
            "<sources>\nHi\n</sources>",
            {"xml_tag_handlers": {"sources": None}},
        )
        assert all(block["type"] != "container" for block in disabled)
    finally:
        clear_xml_tag_handlers()


def test_unclosed_tag_runs_until_next_registered_or_end():
    blocks = markdown_to_blocks(
        "Keep this\n\n<sources>\nno close\n\nStill here",
        {"xml_tag_handlers": {"sources": _sources}},
    )
    assert blocks[0]["text"]["text"] == "Keep this"
    assert blocks[1]["type"] == "container"
    inside = " ".join(block["text"]["text"] for block in blocks[1]["child_blocks"])
    assert "no close" in inside and "Still here" in inside
    assert "<sources>" not in inside

    nested = markdown_to_blocks(
        "<detailed>\ninside detailed\n\n<sources>\nitem\n</sources>\n",
        HANDLERS,
    )
    assert [block["type"] for block in nested] == ["container", "container"]
    assert nested[0]["title"]["text"] == "Details"
    assert "inside detailed" in nested[0]["child_blocks"][0]["text"]["text"]
    assert nested[1]["title"]["text"] == "Sources"
    assert nested[1]["child_blocks"][0]["text"]["text"] == "item"


def test_stray_registered_close_is_dropped():
    blocks = markdown_to_blocks(
        "Before </sources> and </detailed> after </not-opened>",
        HANDLERS,
    )
    text = blocks[0]["text"]["text"]
    assert "</sources>" not in text
    assert "</detailed>" not in text
    assert '&lt;/not-opened&gt;' in text
    assert text.startswith("Before") and text.endswith("&lt;/not-opened&gt;")


def test_llm_text_mixes_comparisons_closed_and_broken_tags():
    """Prose, comparisons, a real element, a stray closer, then an unclosed tag.

    The unclosed tag sits *before* a second well-formed element, which is the
    shape an LLM produces when it opens a region and never finishes it.
    """
    markdown = """The check fails when load < 5 and latency > 200ms. 2<5 is also true.

Ignore this stray closer </sources> and the mismatch </detailed>.

<sources title="Runbook">
Retry while attempts < 3 and queue > 0.
- [Ops guide](https://example.com/ops)
</sources>

cpu < 90 is fine, and so is disk > 10. This opening tag never closes:

<detailed>
1 < 2 should stay visible, and so should the element after it.

<sources>
plain note with 4 > 1
</sources>

</not-opened>
"""
    blocks = markdown_to_blocks(markdown, HANDLERS)
    containers = [block for block in blocks if block["type"] == "container"]
    texts = _block_texts(blocks)
    joined = "\n".join(texts)

    assert [block["title"]["text"] for block in containers] == [
        "Runbook",
        "Details",
        "Sources",
    ]
    assert containers[0]["child_blocks"][0]["text"]["text"] == (
        "Retry while attempts &lt; 3 and queue &gt; 0."
    )
    link = containers[0]["child_blocks"][1]["elements"][0]["elements"][0]["elements"][0]
    assert link["url"] == "https://example.com/ops"
    assert containers[2]["child_blocks"][0]["text"]["text"] == "plain note with 4 &gt; 1"
    assert '1 &lt; 2' in containers[1]["child_blocks"][0]["text"]["text"]
    assert all(block["type"] != "container" or block["title"]["text"] != "Details" or "plain note" not in str(block["child_blocks"]) for block in containers)

    assert texts[0] == "The check fails when load &lt; 5 and latency &gt; 200ms. 2&lt;5 is also true."
    assert "</sources>" not in joined and "</detailed>" not in joined
    assert "stray closer" in joined and "mismatch" in joined
    assert any("cpu &lt; 90" in text and "disk &gt; 10" in text for text in texts)
    assert any("</not-opened>" in text or "&lt;/not-opened&gt;" in text for text in texts)


def test_container_helper_optional_fields():
    block = container_block(
        "Title",
        [{"type": "divider"}],
        subtitle="Sub",
        width="wide",
        block_id="sources-1",
        collapsible=True,
        default_collapsed=True,
    )
    assert block["width"] == "wide"
    assert block["block_id"] == "sources-1"
    assert block["subtitle"]["text"] == "Sub"
    assert block["child_blocks"] == [{"type": "divider"}]
