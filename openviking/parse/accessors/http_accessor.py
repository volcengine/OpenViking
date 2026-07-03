# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
HTTP URL Accessor.

Fetches HTTP/HTTPS URLs and makes them available as local files.
This is the DataAccessor layer extracted from HTMLParser.

Features:
- Downloads web pages to local HTML files
- Downloads files (PDF, Markdown, etc.) to local files
- Supports GitHub/GitLab blob to raw URL conversion
- Follows redirects
- Network guard integration
- Detailed error classification (network, timeout, auth, etc.)
- IANA Media Type (MIME) based content detection for URLs without file extensions
"""

import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from openviking.parse.base import lazy_import
from openviking.parse.parsers.constants import CODE_EXTENSIONS
from openviking.parse.parsers.media.constants import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from openviking.utils import is_code_hosting_blob_url
from openviking.utils.network_guard import build_httpx_request_validation_hooks
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.logger import get_logger

from .base import DataAccessor, LocalResource, SourceType
from .mime_types import MEDIA_TYPE_ALIASES, IANAMediaType, get_preferred_extension

logger = get_logger(__name__)

DOCUMENT_EXTENSIONS = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".epub",
    ".zip",
}


class URLType(Enum):
    """URL content types for routing to appropriate parsers."""

    WEBPAGE = "webpage"  # HTML webpage to parse
    DOWNLOAD_PDF = "download_pdf"  # PDF file download link
    DOWNLOAD_MD = "download_md"  # Markdown file download link
    DOWNLOAD_TXT = "download_txt"  # Text file download link
    DOWNLOAD_HTML = "download_html"  # HTML file download link
    DOWNLOAD_IMAGE = "download_image"  # Image file download link
    DOWNLOAD_AUDIO = "download_audio"  # Audio file download link
    DOWNLOAD_VIDEO = "download_video"  # Video file download link
    DOWNLOAD_DOCUMENT = "download_document"  # Office/e-book/archive document link
    UNKNOWN = "unknown"  # Unknown or unsupported type


class URLTypeDetector:
    """
    Detector for URL content types.

    Uses IANA Media Type (MIME) standards for robust content detection:
    1. Check file extension (fast path)
    2. Check Content-Disposition header for filename (most reliable)
    3. Check Content-Type header (IANA standard media types)
    4. Fall back to default behavior

    References:
        - RFC 6838: Media Type Specifications and Registration Procedures
        - RFC 6266: Use of the Content-Disposition Header Field in HTTP
    """

    # === Extension to URL type mapping ===
    # CODE_EXTENSIONS spread comes first so explicit entries below override
    # (e.g., .html/.htm -> DOWNLOAD_HTML instead of DOWNLOAD_TXT)
    EXTENSION_MAP: Dict[str, URLType] = {
        **dict.fromkeys(CODE_EXTENSIONS, URLType.DOWNLOAD_TXT),
        ".pdf": URLType.DOWNLOAD_PDF,
        ".md": URLType.DOWNLOAD_MD,
        ".markdown": URLType.DOWNLOAD_MD,
        ".txt": URLType.DOWNLOAD_TXT,
        ".text": URLType.DOWNLOAD_TXT,
        ".html": URLType.DOWNLOAD_HTML,
        ".htm": URLType.DOWNLOAD_HTML,
        **dict.fromkeys(IMAGE_EXTENSIONS, URLType.DOWNLOAD_IMAGE),
        **dict.fromkeys(AUDIO_EXTENSIONS, URLType.DOWNLOAD_AUDIO),
        **dict.fromkeys(VIDEO_EXTENSIONS, URLType.DOWNLOAD_VIDEO),
        **dict.fromkeys(DOCUMENT_EXTENSIONS, URLType.DOWNLOAD_DOCUMENT),
    }

    # === IANA Media Type to URL type mapping ===
    # Maps IANA registered media types to our internal URLType
    # Patterns can be:
    #   - Exact match: "application/pdf"
    #   - Wildcard: "text/*"
    #   - Type only: "image" (treated as "image/*")
    # NOTE: .html/.htm extensions are mapped to DOWNLOAD_HTML via EXTENSION_MAP,
    #       while text/html Content-Type is mapped to WEBPAGE here for URLs
    #       without extensions (like https://example.com/page)
    MEDIA_TYPE_MAP: Dict[str, URLType] = {
        # PDF
        "application/pdf": URLType.DOWNLOAD_PDF,
        # Markdown
        "text/markdown": URLType.DOWNLOAD_MD,
        "text/x-markdown": URLType.DOWNLOAD_MD,
        # HTML/webpage (for URLs without .html extension)
        "text/html": URLType.WEBPAGE,
        "application/xhtml+xml": URLType.WEBPAGE,
        # Plain text
        "text/plain": URLType.DOWNLOAD_TXT,
        "text/*": URLType.DOWNLOAD_TXT,
        # Media files. Some signed object-storage URLs reject HEAD, but for
        # extensionless URLs that do return headers this routes to media parsers.
        "image/*": URLType.DOWNLOAD_IMAGE,
        "audio/*": URLType.DOWNLOAD_AUDIO,
        "video/*": URLType.DOWNLOAD_VIDEO,
        # Document formats supported by ParserRegistry. The final extension
        # still comes from URL path, Content-Disposition, or IANA media type.
        "application/msword": URLType.DOWNLOAD_DOCUMENT,
        "application/vnd.ms-excel": URLType.DOWNLOAD_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": URLType.DOWNLOAD_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": URLType.DOWNLOAD_DOCUMENT,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": URLType.DOWNLOAD_DOCUMENT,
        "application/epub+zip": URLType.DOWNLOAD_DOCUMENT,
        "application/zip": URLType.DOWNLOAD_DOCUMENT,
    }

    # URLType to file extension mapping
    URL_TYPE_TO_EXT: Dict[URLType, str] = {
        URLType.WEBPAGE: ".html",
        URLType.DOWNLOAD_PDF: ".pdf",
        URLType.DOWNLOAD_MD: ".md",
        URLType.DOWNLOAD_TXT: ".txt",
        URLType.DOWNLOAD_HTML: ".html",
        URLType.DOWNLOAD_IMAGE: ".png",
        URLType.DOWNLOAD_AUDIO: ".mp3",
        URLType.DOWNLOAD_VIDEO: ".mp4",
        URLType.DOWNLOAD_DOCUMENT: ".zip",
        URLType.UNKNOWN: ".html",
    }

    def __init__(self, timeout: float = 10.0):
        """Initialize URL type detector."""
        self.timeout = timeout

    async def detect(
        self,
        url: str,
        timeout: Optional[float] = None,
        request_validator=None,
    ) -> Tuple[URLType, Dict[str, Any]]:
        """
        Detect URL content type using IANA standards.

        Detection order (most reliable to least reliable):
        1. File extension from URL path (if valid and recognized)
        2. Filename from Content-Disposition header (RFC 6266)
        3. IANA Media Type from Content-Type header (RFC 6838)
        4. Default to WEBPAGE

        Args:
            url: URL to detect
            timeout: HTTP request timeout in seconds (optional, overrides detector's default)
            request_validator: Optional network request validator

        Returns:
            (URLType, metadata dict with detection details)
        """
        meta = {
            "url": url,
            "detected_by": "unknown",
        }
        parsed = urlparse(url)
        path_lower = parsed.path.lower()
        valid_extensions = set(self.EXTENSION_MAP.keys())

        # === Step 1: Check extension from URL path ===
        path_ext = Path(path_lower).suffix
        if path_ext and path_ext in valid_extensions:
            for ext, url_type in self.EXTENSION_MAP.items():
                if path_lower.endswith(ext):
                    meta["detected_by"] = "extension"
                    meta["extension"] = ext
                    return url_type, meta

        # === Step 2: Send HEAD request for headers ===
        try:
            httpx = lazy_import("httpx")
            client_kwargs = {
                "timeout": timeout if timeout is not None else self.timeout,
                "follow_redirects": True,
            }
            event_hooks = build_httpx_request_validation_hooks(request_validator)
            if event_hooks:
                client_kwargs["event_hooks"] = event_hooks
                client_kwargs["trust_env"] = False

            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.head(url)

                meta["status_code"] = response.status_code
                if not (200 <= response.status_code < 300):
                    meta["head_status_skipped"] = response.status_code
                    raise RuntimeError(f"HEAD returned {response.status_code}; headers not trusted")

                # === Step 2a/2b: Check Content-Disposition and Content-Type ===
                url_type = self.detect_from_headers(response.headers, meta)
                if url_type != URLType.UNKNOWN:
                    return url_type, meta

        except PermissionDeniedError:
            raise
        except Exception as e:
            meta["detection_error"] = str(e)
            logger.debug(f"[URLTypeDetector] HEAD request failed: {e}, falling back to default")

        # === Step 3: Default behavior ===
        meta["detected_by"] = "default"
        return URLType.WEBPAGE, meta

    def detect_from_headers(
        self,
        headers: Mapping[str, str],
        meta: Dict[str, Any],
        detected_by_prefix: str = "",
    ) -> URLType:
        """
        Detect URL type from HTTP response headers.

        Args:
            headers: Response headers from HEAD or GET.
            meta: Metadata dict to update.
            detected_by_prefix: Optional prefix for meta["detected_by"], e.g. "get_".

        Returns:
            Detected URLType, or URLType.UNKNOWN if no header matched.
        """
        content_type_raw = headers.get("content-type", "")
        content_disposition = headers.get("content-disposition", "")

        meta["content_type_raw"] = content_type_raw
        meta["content_disposition_raw"] = content_disposition

        # Check Content-Disposition for filename (RFC 6266)
        filename_from_disposition = self._extract_filename_from_disposition(content_disposition)
        if filename_from_disposition:
            meta["filename_from_disposition"] = filename_from_disposition
            filename_lower = filename_from_disposition.lower()
            for ext, url_type in self.EXTENSION_MAP.items():
                if filename_lower.endswith(ext):
                    meta["detected_by"] = f"{detected_by_prefix}content_disposition"
                    meta["extension"] = ext
                    return url_type

        # Check Content-Type (RFC 6838)
        if content_type_raw:
            url_type = self._detect_from_media_type(
                content_type_raw,
                meta,
                detected_by_prefix=detected_by_prefix,
            )
            if url_type != URLType.UNKNOWN:
                return url_type

        return URLType.UNKNOWN

    def _detect_from_media_type(
        self,
        content_type: str,
        meta: Dict[str, Any],
        detected_by_prefix: str = "",
    ) -> URLType:
        """
        Detect URL type from IANA media type.

        Args:
            content_type: Content-Type header value
            meta: Metadata dict to update

        Returns:
            Detected URLType, or URLType.UNKNOWN if no match
        """
        # Normalize and parse according to IANA standards
        media_type_str = content_type.lower().strip()

        # Handle common aliases
        if media_type_str in MEDIA_TYPE_ALIASES:
            meta["media_type_alias"] = media_type_str
            media_type_str = MEDIA_TYPE_ALIASES[media_type_str]

        # Parse into structured IANAMediaType
        try:
            media_type = IANAMediaType.parse(media_type_str)
            meta["media_type"] = str(media_type)
            meta["media_type_type"] = media_type.type
            meta["media_type_subtype"] = media_type.subtype
            if media_type.suffix:
                meta["media_type_suffix"] = media_type.suffix
        except Exception as e:
            logger.debug(f"[URLTypeDetector] Failed to parse media type: {e}")
            meta["media_type_parse_error"] = str(e)
            return URLType.UNKNOWN

        # Check for exact match first
        media_type_key = f"{media_type.type}/{media_type.subtype}"
        if media_type.suffix:
            media_type_with_suffix = f"{media_type_key}+{media_type.suffix}"
            if media_type_with_suffix in self.MEDIA_TYPE_MAP:
                meta["detected_by"] = f"{detected_by_prefix}media_type_suffix"
                return self.MEDIA_TYPE_MAP[media_type_with_suffix]

        if media_type_key in self.MEDIA_TYPE_MAP:
            meta["detected_by"] = f"{detected_by_prefix}media_type"
            return self.MEDIA_TYPE_MAP[media_type_key]

        # Check for wildcard matches
        for pattern, url_type in self.MEDIA_TYPE_MAP.items():
            if media_type.matches(pattern):
                meta["detected_by"] = f"{detected_by_prefix}media_type_pattern"
                meta["media_type_pattern"] = pattern
                return url_type

        return URLType.UNKNOWN

    @staticmethod
    def _extract_filename_from_disposition(content_disposition: str) -> Optional[str]:
        """
        Extract filename from Content-Disposition header per RFC 6266.

        Handles formats:
            - inline; filename="2601.00014v1.pdf"
            - attachment; filename=document.pdf
            - attachment; filename*=UTF-8''encoded.pdf
            - attachment; filename="foo.pdf"; size=12345

        Args:
            content_disposition: Content-Disposition header value

        Returns:
            Extracted filename, or None if not found
        """
        if not content_disposition:
            return None

        import re

        content_disposition = content_disposition.strip()

        # Try filename*=UTF-8''... format first (RFC 5987)
        utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.I)
        if utf8_match:
            from urllib.parse import unquote

            return unquote(utf8_match.group(1))

        # Try filename="..." format (quoted-string)
        quoted_match = re.search(r'filename="([^"]+)"', content_disposition, re.I)
        if quoted_match:
            return quoted_match.group(1)

        # Try filename=... format (token)
        simple_match = re.search(r"filename=([^;]+)", content_disposition, re.I)
        if simple_match:
            return simple_match.group(1).strip()

        return None

    def get_extension_for_type(self, url_type: URLType) -> str:
        """Get file extension for URL type."""
        return self.URL_TYPE_TO_EXT.get(url_type, ".html")


class HTTPAccessor(DataAccessor):
    """
    Accessor for HTTP/HTTPS URLs.

    Features:
    - Downloads web pages to local HTML files
    - Downloads files (PDF, Markdown, etc.) to local files
    - Supports GitHub/GitLab blob to raw URL conversion
    - Follows redirects
    - Network guard integration
    - Detailed error classification (network, timeout, auth, etc.)
    - IANA Media Type based detection for URLs without extensions
    """

    PRIORITY = 50  # Lower than GitAccessor, higher than fallback

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
    ):
        """Initialize HTTP accessor."""
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self._url_detector = URLTypeDetector(timeout=min(timeout, 10.0))

    @property
    def priority(self) -> int:
        return self.PRIORITY

    def can_handle(self, source: Union[str, Path], **kwargs) -> bool:
        """
        Check if this accessor can handle the source.

        Handles any HTTP/HTTPS URL.
        NOTE: GitAccessor and FeishuAccessor have higher priority
        and will be checked first for their specific URL types.
        """
        source_str = str(source)
        return source_str.startswith(("http://", "https://"))

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Fetch the HTTP URL to a local file or directory.

        Args:
            source: HTTP/HTTPS URL
            **kwargs: Additional arguments (request_validator, etc.)

        Returns:
            LocalResource pointing to the downloaded file or crawled web directory
        """
        source_str = str(source)
        request_validator = kwargs.get("request_validator")

        # Download the URL
        temp_path, url_type, meta = await self._download_url(
            source_str,
            request_validator=request_validator,
        )

        # Both an extensionless text/html page (WEBPAGE) and an explicit
        # ``.html``/``.htm`` URL (DOWNLOAD_HTML) are webpages the user may want
        # to crawl: route both through WebImporter so ``depth``/``max_pages``
        # apply. A plain single-page import is just the ``depth=0`` case.
        #
        # Exception: a code-hosting single-file URL (GitHub/GitLab ``blob`` or
        # GitHub ``raw``) is semantically one file, not a site. ``_download_url``
        # already rewrote it to raw and fetched the file, so keep it on the
        # single-file path instead of crawling the hosting UI shell.
        if url_type in (URLType.WEBPAGE, URLType.DOWNLOAD_HTML) and not is_code_hosting_blob_url(
            source_str
        ):
            from openviking.parse.accessors.web_importer import (
                WebImporter,
                parse_web_import_options,
            )

            try:
                options = parse_web_import_options(kwargs)
                result = await WebImporter().import_to_directory(
                    root_url=source_str,
                    options=options,
                    request_validator=request_validator,
                )
            finally:
                Path(temp_path).unlink(missing_ok=True)

            meta.update(result.meta)
            meta.update(
                {
                    "url": source_str,
                    "downloaded": True,
                    "url_type": URLType.WEBPAGE.value,
                }
            )
            return LocalResource(
                path=result.path,
                source_type=SourceType.HTTP,
                original_source=source_str,
                meta=meta,
                is_temporary=True,
            )

        # Build metadata
        meta.update(
            {
                "url": source_str,
                "downloaded": True,
                "url_type": url_type.value,
            }
        )

        return LocalResource(
            path=Path(temp_path),
            source_type=SourceType.HTTP,
            original_source=source_str,
            meta=meta,
            is_temporary=True,
        )

    @staticmethod
    def _extract_filename_from_url(url: str) -> str:
        """
        Extract and URL-decode the original filename from a URL.

        Args:
            url: URL to extract filename from

        Returns:
            Decoded filename (e.g., "schemas.py" from ".../schemas.py")
            Falls back to "download" if no filename can be extracted.
        """
        parsed = urlparse(url)
        # URL-decode path to handle encoded characters (e.g., %E7%99%BE -> Chinese chars)
        decoded_path = unquote(parsed.path)
        basename = Path(decoded_path).name
        return basename if basename else "download"

    async def _download_url(
        self,
        url: str,
        request_validator=None,
    ) -> Tuple[str, URLType, Dict[str, Any]]:
        """
        Download URL content to a temporary file.

        Args:
            url: URL to download
            request_validator: Optional network request validator

        Returns:
            Tuple of (path to temporary file, URLType, metadata dict)
        """
        httpx = lazy_import("httpx")

        # Convert GitHub/GitLab blob URLs to raw
        url = self._convert_to_raw_url(url)

        # Detect URL type first to get proper extension
        url_type, detect_meta = await self._url_detector.detect(
            url,
            request_validator=request_validator,
        )

        temp_path: Optional[str] = None

        try:
            # Download content
            client_kwargs = {
                "timeout": self.timeout,
                "follow_redirects": True,
            }
            event_hooks = build_httpx_request_validation_hooks(request_validator)
            if event_hooks:
                client_kwargs["event_hooks"] = event_hooks
                client_kwargs["trust_env"] = False

            async with httpx.AsyncClient(**client_kwargs) as client:
                headers = {"User-Agent": self.user_agent}
                try:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                except httpx.ConnectError as e:
                    user_msg = "HTTP request failed: could not connect to server. Check the URL or your network."
                    raise RuntimeError(f"{user_msg} URL: {url}. Details: {e}") from e
                except httpx.TimeoutException as e:
                    user_msg = "HTTP request failed: timeout. The server took too long to respond."
                    raise RuntimeError(f"{user_msg} URL: {url}. Details: {e}") from e
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code if e.response else "unknown"
                    if status_code == 401 or status_code == 403:
                        user_msg = f"HTTP request failed: authentication error ({status_code}). Check your credentials or permissions."
                    elif status_code == 404:
                        user_msg = f"HTTP request failed: not found ({status_code}). The URL may be invalid or the resource was removed."
                    elif 500 <= status_code < 600:
                        user_msg = f"HTTP request failed: server error ({status_code}). The server encountered an error."
                    else:
                        user_msg = f"HTTP request failed: status code {status_code}."
                    raise RuntimeError(f"{user_msg} URL: {url}. Details: {e}") from e
                except Exception as e:
                    user_msg = "HTTP request failed: unexpected error."
                    raise RuntimeError(f"{user_msg} URL: {url}. Details: {e}") from e

                meta = self._finalize_download_metadata(
                    url=url,
                    initial_url_type=url_type,
                    initial_meta=detect_meta,
                    response_headers=response.headers,
                    content=response.content,
                )
                url_type = meta["resolved_url_type"]
                ext = meta["extension"]

                # Create temp file after GET headers/content have had a chance
                # to refine detection. This avoids routing extensionless signed
                # URLs as HTML when HEAD is rejected by object storage.
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                temp_path = temp_file.name
                temp_file.close()

                # Write to temp file
                Path(temp_path).write_bytes(response.content)

            return temp_path, url_type, meta
        except Exception:
            # Clean up on error
            if temp_path:
                try:
                    p = Path(temp_path)
                    if p.exists():
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
            raise

    def _finalize_download_metadata(
        self,
        url: str,
        initial_url_type: URLType,
        initial_meta: Dict[str, Any],
        response_headers: Mapping[str, str],
        content: bytes,
    ) -> Dict[str, Any]:
        """Resolve final URL type, extension, and filename using GET data."""
        meta = dict(initial_meta)
        url_type = initial_url_type

        get_meta: Dict[str, Any] = {}
        get_url_type = self._url_detector.detect_from_headers(
            response_headers,
            get_meta,
            detected_by_prefix="get_",
        )
        if get_url_type != URLType.UNKNOWN and self._should_refine_url_type(url_type, get_url_type):
            url_type = get_url_type
            meta.update(get_meta)
            meta["refined_by_get_headers"] = True
        else:
            meta.setdefault("get_content_type_raw", response_headers.get("content-type", ""))
            meta.setdefault(
                "get_content_disposition_raw",
                response_headers.get("content-disposition", ""),
            )
            if get_meta.get("filename_from_disposition"):
                meta.setdefault(
                    "get_filename_from_disposition",
                    get_meta["filename_from_disposition"],
                )

        magic_url_type, magic_ext = self._detect_from_magic_bytes(content)
        if magic_url_type != URLType.UNKNOWN and self._should_use_magic_bytes(
            url_type, initial_meta
        ):
            url_type = magic_url_type
            meta["detected_by"] = "magic_bytes"
            meta["magic_extension"] = magic_ext
            meta["refined_by_magic_bytes"] = True

        meta["resolved_url_type"] = url_type
        if meta.get("refined_by_magic_bytes") and magic_ext:
            ext = magic_ext
        else:
            ext = self._determine_file_extension(url, url_type, meta)

        original_filename = meta.get("filename_from_disposition")
        if not original_filename:
            original_filename = self._extract_filename_from_url(url)
        if original_filename == "download" and ext and ext != ".html":
            original_filename = f"download{ext}"

        meta.update({"extension": ext, "original_filename": original_filename})
        return meta

    @staticmethod
    def _should_refine_url_type(current: URLType, candidate: URLType) -> bool:
        """Only replace ambiguous/default webpage guesses with file types."""
        if candidate in (URLType.UNKNOWN, URLType.WEBPAGE):
            return False
        if current in (URLType.UNKNOWN, URLType.WEBPAGE):
            return True
        return current == candidate

    @staticmethod
    def _should_use_magic_bytes(current: URLType, initial_meta: Dict[str, Any]) -> bool:
        """Use binary signatures for ambiguous detections, but not explicit URL extensions."""
        if initial_meta.get("detected_by") == "extension":
            return False
        return current in {
            URLType.UNKNOWN,
            URLType.WEBPAGE,
            URLType.DOWNLOAD_TXT,
            URLType.DOWNLOAD_HTML,
        }

    @staticmethod
    def _detect_from_magic_bytes(content: bytes) -> Tuple[URLType, Optional[str]]:
        """Detect common binary types when headers are unavailable or generic."""
        sample = content[:16]
        if sample.startswith(b"\x89PNG\r\n\x1a\n"):
            return URLType.DOWNLOAD_IMAGE, ".png"
        if sample.startswith(b"\xff\xd8\xff"):
            return URLType.DOWNLOAD_IMAGE, ".jpg"
        if sample.startswith(b"GIF87a") or sample.startswith(b"GIF89a"):
            return URLType.DOWNLOAD_IMAGE, ".gif"
        if sample.startswith(b"BM"):
            return URLType.DOWNLOAD_IMAGE, ".bmp"
        if sample.startswith(b"RIFF") and len(sample) >= 12:
            riff_type = sample[8:12]
            if riff_type == b"WEBP":
                return URLType.DOWNLOAD_IMAGE, ".webp"
            if riff_type == b"WAVE":
                return URLType.DOWNLOAD_AUDIO, ".wav"
            if riff_type == b"AVI ":
                return URLType.DOWNLOAD_VIDEO, ".avi"
        if sample.startswith(b"ID3") or sample.startswith(b"\xff\xfb"):
            return URLType.DOWNLOAD_AUDIO, ".mp3"
        if len(sample) >= 12 and sample[4:8] == b"ftyp":
            brand = sample[8:12].lower()
            if brand in {b"qt  ", b"moov"}:
                return URLType.DOWNLOAD_VIDEO, ".mov"
            return URLType.DOWNLOAD_VIDEO, ".mp4"
        if sample.startswith(b"\x1f\x8b"):
            return URLType.UNKNOWN, ".gz"
        svg_sample = content[:512].lstrip()
        if svg_sample.startswith(b"<svg") or (
            svg_sample.startswith(b"<?xml") and b"<svg" in svg_sample
        ):
            return URLType.DOWNLOAD_IMAGE, ".svg"
        if sample.startswith(b"%PDF-"):
            return URLType.DOWNLOAD_PDF, ".pdf"
        if sample.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return URLType.DOWNLOAD_DOCUMENT, ".doc"
        if (
            sample.startswith(b"PK\x03\x04")
            or sample.startswith(b"PK\x05\x06")
            or sample.startswith(b"PK\x07\x08")
        ):
            return URLType.DOWNLOAD_DOCUMENT, HTTPAccessor._detect_zip_based_extension(content)
        return URLType.UNKNOWN, None

    @staticmethod
    def _detect_zip_based_extension(content: bytes) -> str:
        """Differentiate Office/EPUB/ZIP files that all share the ZIP signature."""
        try:
            from io import BytesIO
            from zipfile import ZipFile

            with ZipFile(BytesIO(content)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" in names or any(name.startswith("word/") for name in names):
                    return ".docx"
                if "xl/workbook.xml" in names or any(name.startswith("xl/") for name in names):
                    return ".xlsx"
                if "ppt/presentation.xml" in names or any(
                    name.startswith("ppt/") for name in names
                ):
                    return ".pptx"
                if "mimetype" in names:
                    try:
                        if zf.read("mimetype", 64) == b"application/epub+zip":
                            return ".epub"
                    except Exception:
                        pass
        except Exception:
            return ".zip"
        return ".zip"

    def _determine_file_extension(
        self,
        url: str,
        url_type: URLType,
        detect_meta: Dict[str, Any],
    ) -> str:
        """
        Determine appropriate file extension using multiple strategies.

        Strategy order (most reliable first):
        1. Extension from Content-Disposition filename
        2. Extension from URL path (if valid)
        3. Use IANA media type mapping
        4. Use URL type based extension

        Args:
            url: Original URL
            url_type: Detected URL type
            detect_meta: Detection metadata

        Returns:
            File extension including dot (e.g., ".pdf")
        """
        valid_extensions = set(URLTypeDetector.EXTENSION_MAP.keys())

        # 1. Try extension from Content-Disposition filename
        filename_from_disposition = detect_meta.get("filename_from_disposition")
        if filename_from_disposition:
            ext = Path(filename_from_disposition.lower()).suffix
            if ext and ext in valid_extensions:
                return ext

        # 2. Try extension from URL path (if valid)
        parsed = urlparse(url)
        decoded_path = unquote(parsed.path)
        ext = Path(decoded_path).suffix
        if ext and ext.lower() in valid_extensions:
            return ext.lower()

        # 3. Try IANA media type to extension mapping
        media_type_str = detect_meta.get("media_type") or detect_meta.get("content_type_raw")
        if media_type_str:
            iana_ext = get_preferred_extension(media_type_str)
            if iana_ext:
                return iana_ext

        # 4. Fall back to URL type based extension
        return self._url_detector.get_extension_for_type(url_type)

    def _convert_to_raw_url(self, url: str) -> str:
        """Convert GitHub/GitLab blob URL to raw URL."""
        parsed = urlparse(url)
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            # NOTE: github_domains/gitlab_domains are in CodeConfig, not HTMLConfig
            github_domains = config.code.github_domains
            gitlab_domains = config.code.gitlab_domains
            github_raw_domain = config.code.github_raw_domain

            if parsed.netloc in github_domains:
                path_parts = parsed.path.strip("/").split("/")
                if len(path_parts) >= 4 and path_parts[2] == "blob":
                    # Remove 'blob'
                    new_path = "/".join(path_parts[:2] + path_parts[3:])
                    return f"https://{github_raw_domain}/{new_path}"

            if parsed.netloc in gitlab_domains and "/blob/" in parsed.path:
                return url.replace("/blob/", "/raw/")

        except Exception as e:
            logger.debug(
                f"[HTTPAccessor] Failed to convert blob URL to raw: {e}, "
                f"falling back to original URL: {url}"
            )

        return url
