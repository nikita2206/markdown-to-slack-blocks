"""Convert Markdown to Slack Block Kit JSON, and back.

Python port of `markdown-to-slack-blocks` (MIT). Behavior tracks the
JavaScript library at version 1.6.1. This package's own release is 1.0.0.
"""

from .parser import markdown_to_blocks
from .splitter import (
    blocks_to_markdown,
    blocks_to_plain_text,
    split_blocks,
    split_blocks_with_text,
)
from .tags import (
    XmlTagContext,
    clear_xml_tag_handlers,
    container_block,
    register_xml_tag_handler,
)
from .validator import validate_blocks_to_markdown_options, validate_options

__all__ = [
    "XmlTagContext",
    "blocks_to_markdown",
    "blocks_to_plain_text",
    "clear_xml_tag_handlers",
    "container_block",
    "markdown_to_blocks",
    "register_xml_tag_handler",
    "split_blocks",
    "split_blocks_with_text",
    "validate_blocks_to_markdown_options",
    "validate_options",
]

# Aliases matching the JavaScript export names.
markdownToBlocks = markdown_to_blocks
splitBlocks = split_blocks
splitBlocksWithText = split_blocks_with_text
blocksToMarkdown = blocks_to_markdown
blocksToPlainText = blocks_to_plain_text
validateOptions = validate_options
validateBlocksToMarkdownOptions = validate_blocks_to_markdown_options

__version__ = "1.0.0"
