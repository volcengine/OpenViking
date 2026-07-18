# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ParserRouter: Route parsing requests between ParserRegistry and UnderstandingAPI.

Routing is controlled by ov.conf (OpenVikingConfig.parser_api).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Union
from urllib.parse import urlparse

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource

from openviking.parse.accessors.base import SourceType
from openviking.parse.base import ParseResult
from openviking.parse.parsers.constants import CODE_EXTENSIONS
from openviking.parse.parsers.media.constants import MEDIA_EXTENSIONS
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ParserRouter:
    """
    ParserRouter: Route parsing to internal ParserRegistry or third-party UnderstandingAPI.

    Routing logic:
    1. Check feature flag and extension whitelist
    2. Default: ParserRegistry
    3. Matched extensions: UnderstandingAPI
    """

    def __init__(self, parser_registry: ParserRegistry):
        self._parser_registry = parser_registry
        self._understanding_api = None

    def should_use_understanding_api(self, source_path: Union[str, Path]) -> bool:
        """
        Decide whether to use UnderstandingAPI.
        """
        try:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            ov_config = get_openviking_config()
        except Exception:
            return False

        parser_api = getattr(ov_config, "parser_api", None)
        if not parser_api or not getattr(parser_api, "enable", False):
            return False

        ext = self._extract_extension(source_path)
        extensions = getattr(parser_api, "extensions", None) or []
        return ext in extensions

    def _extract_extension(self, source_path: Union[str, Path]) -> str:
        source = str(source_path)
        parsed = urlparse(source)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            source = parsed.path
        return Path(source).suffix.lower().lstrip(".")

    async def parse(self, source: Union[str, Path, "LocalResource"], **kwargs) -> ParseResult:
        """
        Parse with ParserRegistry or UnderstandingAPI based on the routing decision.
        """
        source_path = self._extract_source_path(source)
        prefer_local_code = self._is_local_code_media_conflict(source, source_path)

        if self.should_use_understanding_api(source_path) and not prefer_local_code:
            display = source_path
            if isinstance(source_path, str) and source_path.startswith(("http://", "https://")):
                display = "<url>"
            else:
                try:
                    display = Path(source_path).name
                except Exception:
                    display = "<path>"
            logger.info(f"[ParserRouter] Using UnderstandingAPI for {display}")
            return await self._get_understanding_api().parse(str(source_path), **kwargs)
        else:
            try:
                display = Path(source_path).name
            except Exception:
                display = "<path>"
            logger.info(f"[ParserRouter] Using internal ParserRegistry for {display}")

            media_parser_name = self._explicit_remote_media_parser(source, source_path)
            if media_parser_name:
                parser = self._parser_registry.get_parser(media_parser_name)
                if parser is not None:
                    return await parser.parse(source_path, **kwargs)

            return await self._parser_registry.parse(source_path, **kwargs)

    def _is_local_code_media_conflict(
        self,
        source: Union[str, Path, "LocalResource"],
        source_path: Union[str, Path],
    ) -> bool:
        """Whether a local file extension should prefer code over media."""
        if getattr(source, "source_type", None) != SourceType.LOCAL:
            return False
        ext = Path(source_path).suffix.lower()
        return ext in CODE_EXTENSIONS and ext in MEDIA_EXTENSIONS

    def _explicit_remote_media_parser(
        self,
        source: Union[str, Path, "LocalResource"],
        source_path: Union[str, Path],
    ) -> str | None:
        """Restore media routing for an accessor-confirmed ambiguous HTTP file."""
        if getattr(source, "source_type", None) != SourceType.HTTP:
            return None
        ext = Path(source_path).suffix.lower()
        if ext not in CODE_EXTENSIONS or ext not in MEDIA_EXTENSIONS:
            return None
        url_type = getattr(source, "meta", {}).get("url_type")
        return "video" if url_type == "download_video" else None

    def _extract_source_path(self, source: Union[str, Path, "LocalResource"]) -> Union[str, Path]:
        """Extract a filesystem path from the source."""
        if hasattr(source, "path"):
            return source.path
        return source

    def _get_understanding_api(self):
        if self._understanding_api is None:
            from openviking.parse.understanding_api import UnderstandingAPI

            self._understanding_api = UnderstandingAPI()
        return self._understanding_api
