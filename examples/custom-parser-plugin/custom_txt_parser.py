from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class MyCustomTxtParser(BaseParser):
    def __init__(
        self,
        config: Optional[ParserConfig] = None,
        plugin_name: str = "txt parser",
        version: str = "0.0.1",
    ):
        self.plugin_name = plugin_name
        self.version = version

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()
        logger.critical(f"MyCustomTxtParser initialized: {self.plugin_name} {self.version}")

    @property
    def supported_extensions(self) -> List[str]:
        return [".txt"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        logger.critical(f"custom parse txt: {source}, AND ONLY PARSE THE FIRST 500 CHARACTERS")

        path = Path(source)
        if path.exists():
            content = self._read_file(path)[:500] + "..."
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
        result = await self._md_parser.parse_content(
            content, source_path=source_path, instruction=instruction, **kwargs
        )
        result.source_format = "txt"
        result.parser_name = "MyCustomTxtParser"
        return result


__all__ = ["MyCustomTxtParser"]
