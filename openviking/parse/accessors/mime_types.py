# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
IANA Media Type (MIME Type) handling.

References:
- RFC 6838: Media Type Specifications and Registration Procedures
- https://www.iana.org/assignments/media-types/media-types.xhtml
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class IANAMediaType:
    """
    Represents an IANA media type.

    Format: type "/" [tree "."] subtype ["+" suffix] *[";" parameter]

    Examples:
        - text/html
        - application/pdf
        - application/vnd.openxmlformats-officedocument.wordprocessingml.document
        - text/plain; charset=utf-8
    """

    type: str  # The top-level type (text, application, image, etc.)
    subtype: str  # The subtype (may include tree prefix)
    suffix: Optional[str] = None  # Optional structured syntax suffix (e.g., +xml, +json)
    parameters: Dict[str, str] = None  # Optional parameters (e.g., charset=utf-8)

    def __post_init__(self):
        if self.parameters is None:
            object.__setattr__(self, "parameters", {})

    @classmethod
    def parse(cls, media_type: str) -> "IANAMediaType":
        """
        Parse a media type string into an IANAMediaType object.

        Args:
            media_type: Media type string (e.g., "text/plain; charset=utf-8")

        Returns:
            Parsed IANAMediaType object
        """
        media_type = media_type.strip().lower()

        # Split into type/subtype and parameters
        parts = media_type.split(";", 1)
        type_subtype = parts[0].strip()
        params_str = parts[1].strip() if len(parts) > 1 else ""

        # Parse type and subtype
        if "/" not in type_subtype:
            # Invalid format, treat as unknown
            return cls(type="application", subtype="octet-stream")

        type_part, subtype_part = type_subtype.split("/", 1)
        type_part = type_part.strip()
        subtype_part = subtype_part.strip()

        # Check for suffix (e.g., +xml, +json)
        suffix = None
        if "+" in subtype_part:
            subtype_parts = subtype_part.rsplit("+", 1)
            # Only recognize standard suffixes
            if subtype_parts[1] in {
                "xml",
                "json",
                "yaml",
                "ber",
                "fastinfoset",
                "wbxml",
                "zip",
                "cbor",
            }:
                subtype_part = subtype_parts[0]
                suffix = subtype_parts[1]

        # Parse parameters
        parameters = {}
        if params_str:
            for param in params_str.split(";"):
                param = param.strip()
                if not param:
                    continue
                if "=" in param:
                    key, value = param.split("=", 1)
                    parameters[key.strip()] = value.strip()
                else:
                    parameters[param] = ""

        return cls(
            type=type_part,
            subtype=subtype_part,
            suffix=suffix,
            parameters=parameters,
        )

    def matches(self, pattern: str) -> bool:
        """
        Check if this media type matches a pattern.

        Patterns can include wildcards:
            - "text/*" matches any text type
            - "application/*" matches any application type
            - "*/*" matches any type
            - "text/plain" matches exactly
            - "text" is treated as "text/*"

        Args:
            pattern: Pattern to match against

        Returns:
            True if matches, False otherwise
        """
        pattern = pattern.lower().strip()

        if pattern == "*/*":
            return True

        if "/" not in pattern:
            # Treat as type/*
            return self.type == pattern

        pattern_type, pattern_subtype = pattern.split("/", 1)
        pattern_type = pattern_type.strip()
        pattern_subtype = pattern_subtype.strip()

        if pattern_type != "*" and self.type != pattern_type:
            return False

        if pattern_subtype == "*":
            return True

        return self.subtype == pattern_subtype

    def __str__(self) -> str:
        """Return the canonical string representation."""
        parts = [f"{self.type}/{self.subtype}"]
        if self.suffix:
            parts[-1] += f"+{self.suffix}"
        for key, value in self.parameters.items():
            if value:
                parts.append(f"; {key}={value}")
            else:
                parts.append(f"; {key}")
        return "".join(parts)


# IANA registered top-level media types
IANA_TOP_LEVEL_TYPES: Set[str] = {
    "application",
    "audio",
    "example",
    "font",
    "image",
    "message",
    "model",
    "multipart",
    "text",
    "video",
}

# Common media type aliases (non-standard but widely used)
MEDIA_TYPE_ALIASES: Dict[str, str] = {
    "application/x-pdf": "application/pdf",
    "application/x-zip-compressed": "application/zip",
    "application/x-gzip": "application/gzip",
    "application/x-tar": "application/x-tar",
    "text/x-markdown": "text/markdown",
    "text/x-c": "text/plain",
    "text/x-c++": "text/plain",
    "text/x-python": "text/plain",
    "text/x-java": "text/plain",
    "text/x-javascript": "text/plain",
    "text/x-script.python": "text/plain",
    "image/jpg": "image/jpeg",
    "audio/mp3": "audio/mpeg",
    "video/mp4": "video/mp4",  # Keep as is (standard)
}

# Comprehensive IANA media type to file extension mapping
# Based on IANA registry and common usage
IANA_MEDIA_TYPE_TO_EXTENSION: Dict[str, List[str]] = {
    # === Text types ===
    "text/plain": [".txt", ".text"],
    "text/html": [".html", ".htm"],
    "text/css": [".css"],
    "text/csv": [".csv"],
    "text/markdown": [".md", ".markdown"],
    "text/calendar": [".ics"],
    "text/vcard": [".vcf"],
    "text/xml": [".xml"],
    "text/yaml": [".yaml", ".yml"],
    "text/tab-separated-values": [".tsv"],
    "text/sgml": [".sgml"],
    "text/rtf": [".rtf"],
    "text/jinja2": [".j2", ".jinja2"],
    "text/x-yaml": [".yaml", ".yml"],  # Non-standard but common
    "text/x-markdown": [".md", ".markdown"],  # Non-standard but common
    # === Application types - Documents ===
    "application/pdf": [".pdf"],
    "application/rtf": [".rtf"],
    "application/epub+zip": [".epub"],
    "application/x-mobipocket-ebook": [".mobi"],
    # === Application types - Office ===
    "application/msword": [".doc"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "application/vnd.ms-excel": [".xls"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    "application/vnd.ms-powerpoint": [".ppt"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
    "application/vnd.oasis.opendocument.text": [".odt"],
    "application/vnd.oasis.opendocument.spreadsheet": [".ods"],
    "application/vnd.oasis.opendocument.presentation": [".odp"],
    # === Application types - Code ===
    "application/javascript": [".js"],
    "application/json": [".json"],
    "application/ld+json": [".jsonld"],
    "application/xml": [".xml"],
    "application/yaml": [".yaml", ".yml"],
    "application/x-yaml": [".yaml", ".yml"],
    "application/xhtml+xml": [".xhtml"],
    "application/rss+xml": [".rss"],
    "application/atom+xml": [".atom"],
    "application/soap+xml": [".wsdl"],
    # === Application types - Archives ===
    "application/zip": [".zip"],
    "application/x-zip-compressed": [".zip"],
    "application/gzip": [".gz"],
    "application/x-gzip": [".gz"],
    "application/x-tar": [".tar"],
    "application/x-7z-compressed": [".7z"],
    "application/x-rar-compressed": [".rar"],
    "application/x-bzip2": [".bz2"],
    "application/x-lzip": [".lz"],
    "application/x-xz": [".xz"],
    # === Application types - Other ===
    "application/octet-stream": [".bin"],
    "application/pgp-encrypted": [".pgp"],
    "application/pgp-signature": [".sig"],
    "application/pkcs12": [".p12", ".pfx"],
    "application/pkcs8": [".p8"],
    "application/x-pem-file": [".pem"],
    "application/wasm": [".wasm"],
    # === Image types ===
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "image/gif": [".gif"],
    "image/svg+xml": [".svg"],
    "image/webp": [".webp"],
    "image/bmp": [".bmp"],
    "image/tiff": [".tiff", ".tif"],
    "image/vnd.microsoft.icon": [".ico"],
    "image/x-icon": [".ico"],
    "image/heic": [".heic"],
    "image/heif": [".heif"],
    "image/avif": [".avif"],
    # === Audio types ===
    "audio/mpeg": [".mp3", ".mpeg"],
    "audio/mp4": [".m4a", ".mp4a"],
    "audio/ogg": [".ogg", ".oga"],
    "audio/wav": [".wav"],
    "audio/webm": [".webm"],
    "audio/flac": [".flac"],
    "audio/aac": [".aac"],
    "audio/x-wav": [".wav"],
    # === Video types ===
    "video/mp4": [".mp4"],
    "video/mpeg": [".mpeg", ".mpg"],
    "video/quicktime": [".mov"],
    "video/webm": [".webm"],
    "video/x-msvideo": [".avi"],
    "video/x-flv": [".flv"],
    "video/x-matroska": [".mkv"],
    "video/ogg": [".ogv"],
    # === Font types ===
    "font/otf": [".otf"],
    "font/ttf": [".ttf"],
    "font/woff": [".woff"],
    "font/woff2": [".woff2"],
    "application/vnd.ms-fontobject": [".eot"],
    "application/font-woff": [".woff"],
    "application/x-font-ttf": [".ttf"],
    "application/x-font-otf": [".otf"],
    # === Model types ===
    "model/gltf+json": [".gltf"],
    "model/gltf-binary": [".glb"],
    "model/obj": [".obj"],
    "model/stl": [".stl"],
    # === Message types ===
    "message/rfc822": [".eml", ".mht"],
    # === Multipart types ===
    "multipart/mixed": [],
    "multipart/alternative": [],
    "multipart/related": [],
    "multipart/form-data": [],
}


def get_preferred_extension(media_type: str) -> Optional[str]:
    """
    Get the preferred file extension for a media type.

    Args:
        media_type: IANA media type string

    Returns:
        Preferred extension (with dot), or None if unknown
    """
    if not media_type:
        return None

    # Normalize and parse
    media_type = media_type.lower().strip()

    # Handle aliases first
    if media_type in MEDIA_TYPE_ALIASES:
        media_type = MEDIA_TYPE_ALIASES[media_type]

    # Strip parameters
    if ";" in media_type:
        media_type = media_type.split(";", 1)[0].strip()

    # Exact match
    if media_type in IANA_MEDIA_TYPE_TO_EXTENSION:
        exts = IANA_MEDIA_TYPE_TO_EXTENSION[media_type]
        if exts:
            return exts[0]

    # Try parsing for suffix matching
    try:
        parsed = IANAMediaType.parse(media_type)
        # Try with suffix
        if parsed.suffix:
            full_type = f"{parsed.type}/{parsed.subtype}+{parsed.suffix}"
            if full_type in IANA_MEDIA_TYPE_TO_EXTENSION:
                exts = IANA_MEDIA_TYPE_TO_EXTENSION[full_type]
                if exts:
                    return exts[0]
        # Try without tree prefix if it's a vendor type
        if parsed.subtype.startswith("vnd."):
            base_subtype = parsed.subtype[4:]  # Remove "vnd."
            if base_subtype in IANA_MEDIA_TYPE_TO_EXTENSION:
                exts = IANA_MEDIA_TYPE_TO_EXTENSION[base_subtype]
                if exts:
                    return exts[0]
    except Exception:
        pass

    # Try wildcard matches
    if media_type.startswith("text/"):
        return ".txt"
    if media_type.startswith("image/"):
        return ".png"
    if media_type.startswith("audio/"):
        return ".mp3"
    if media_type.startswith("video/"):
        return ".mp4"

    return None


def get_all_extensions(media_type: str) -> List[str]:
    """
    Get all known file extensions for a media type.

    Args:
        media_type: IANA media type string

    Returns:
        List of extensions (with dots), empty list if unknown
    """
    if not media_type:
        return []

    media_type = media_type.lower().strip()

    # Handle aliases first
    if media_type in MEDIA_TYPE_ALIASES:
        media_type = MEDIA_TYPE_ALIASES[media_type]

    # Strip parameters
    if ";" in media_type:
        media_type = media_type.split(";", 1)[0].strip()

    return IANA_MEDIA_TYPE_TO_EXTENSION.get(media_type, [])
