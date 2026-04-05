from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class MyCustomJsonlParser(BaseParser):
    """Example JSONL parser for records with `title` and `content` fields."""

    def __init__(
        self,
        config: Optional[ParserConfig] = None,
    ):
        self.config = config or ParserConfig()
        self._md_parser = MarkdownParser(config=self.config)

        logger.info("MyCustomJsonlParser initialized")

    @property
    def supported_extensions(self) -> List[str]:
        return [".jsonl"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        path = Path(source)
        if path.exists():
            content = self._read_file(path)
            return await self.parse_content(
                content,
                source_path=str(path),
                instruction=instruction,
                **kwargs,
            )

        return await self.parse_content(str(source), instruction=instruction, **kwargs)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        markdown_content = self._jsonl_to_markdown(content)
        result = await self._md_parser.parse_content(
            markdown_content,
            source_path=source_path,
            instruction=instruction,
            **kwargs,
        )
        result.source_format = "jsonl"
        result.parser_name = "MyCustomJsonlParser"
        return result

    def _jsonl_to_markdown(self, content: str) -> str:
        sections = []
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}") from exc

            if not isinstance(record, dict):
                raise ValueError(f"Line {line_number} must be a JSON object")
            if "title" not in record or "content" not in record:
                raise ValueError(f"Line {line_number} must contain 'title' and 'content'")

            sections.append(f"# {record['title']}\n\n{record['content']}")

        return "\n\n".join(sections)


__all__ = ["MyCustomJsonlParser"]
