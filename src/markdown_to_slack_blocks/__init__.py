"""Convert Markdown to Slack Block Kit JSON, and back.

Python port of `markdown-to-slack-blocks` (MIT). Behavior tracks the
JavaScript library at version 1.6.1.
"""

from .parser import markdown_to_blocks
from .splitter import (
    blocks_to_markdown,
    blocks_to_plain_text,
    split_blocks,
    split_blocks_with_text,
)
from .validator import validate_blocks_to_markdown_options, validate_options

__all__ = [
    "blocks_to_markdown",
    "blocks_to_plain_text",
    "markdown_to_blocks",
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

__version__ = "1.6.1"
