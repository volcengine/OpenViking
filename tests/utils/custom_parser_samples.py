from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser


class SampleDocxParser(BaseParser):
    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)

    @property
    def supported_extensions(self) -> List[str]:
        return [".docx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        raise NotImplementedError

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        raise NotImplementedError


class NotAParser:
    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
