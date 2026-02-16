# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
URI utilities for OpenViking.

All context objects in OpenViking are identified by URIs in the format:
viking://<scope>/<path>
"""

import re
from typing import Dict, Optional


class VikingURI:
    """
    Viking URI handler.

    URI Format: viking://<scope>/<path>

    Scopes:
    - resources: Independent resource scope (viking://resources/{project}/...)
    - user: User scope (viking://user/...)
    - agent: Agent scope (viking://agent/...)
    - session: Session scope (viking://session/{session_id}/...)
    - queue: Queue scope (viking://queue/...)

    Examples:
    - viking://resources/my_project/docs/api
    - viking://user/memories/preferences/code_style
    - viking://agent/skills/pdf
    - viking://session/session123/messages
    """

    SCHEME = "viking"
    VALID_SCOPES = {"resources", "user", "agent", "session", "queue", "temp"}

    def __init__(self, uri: str):
        """
        Initialize URI handler.

        Args:
            uri: URI string
        """
        self.uri = uri
        self._parsed = self._parse()

    def _parse(self) -> Dict[str, str]:
        """
        Parse Viking URI into components.

        Returns:
            Dictionary with URI components
        """
        if not self.uri.startswith(f"{self.SCHEME}://"):
            raise ValueError(f"URI must start with '{self.SCHEME}://'")

        # Remove scheme
        path = self.uri[len(f"{self.SCHEME}://") :]

        # Split path components
        parts = path.split("/")
        if len(parts) < 1:
            raise ValueError(f"Invalid URI format: {self.uri}")

        # Parse scope
        scope = parts[0]
        if scope not in self.VALID_SCOPES:
            raise ValueError(f"Invalid scope '{scope}'. Must be one of {self.VALID_SCOPES}")

        return {
            "scheme": self.SCHEME,
            "scope": scope,
            "full_path": path,
        }

    @property
    def scope(self) -> str:
        """Get URI scope."""
        return self._parsed["scope"]

    @property
    def full_path(self) -> str:
        """Get full path (scope + rest)."""
        return self._parsed["full_path"]

    @property
    def resource_name(self) -> Optional[str]:
        """
        Get resource name for resources scope.

        Returns:
            Resource name (e.g., 'my_project' from viking://resources/my_project/...)
            or None for non-resources scopes.
        """
        if self.scope != "resources":
            return None
        parts = self.full_path.split("/")
        return parts[1] if len(parts) > 1 else None

    def matches_prefix(self, prefix: str) -> bool:
        """
        Check if this URI matches a prefix.

        Args:
            prefix: URI prefix to match

        Returns:
            True if matches, False otherwise
        """
        return self.uri.startswith(prefix)

    @property
    def parent(self) -> Optional["VikingURI"]:
        """
        Get parent URI (one level up).

        Returns:
            Parent URI or None if at root
        """
        # Remove trailing slashes
        uri = self.uri.rstrip("/")

        # Find the part after ://
        scheme_sep = "://"
        scheme_end = uri.find(scheme_sep)
        if scheme_end == -1:
            return None

        after_scheme = uri[scheme_end + len(scheme_sep) :]

        # If no / in after_scheme, only scope exists, no path
        if "/" not in after_scheme:
            return None

        # Find last / and truncate
        last_slash = uri.rfind("/")
        return VikingURI(uri[:last_slash]) if last_slash > -1 else None

    @staticmethod
    def is_valid(uri: str) -> bool:
        """
        Check if a URI string is valid.

        Args:
            uri: URI string to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            VikingURI(uri)
            return True
        except ValueError:
            return False

    def join(self, part: str) -> "VikingURI":
        """
        Join URI parts, handling slashes correctly.
        """
        if not part:
            return self

        # Start with first part, strip trailing slash
        result = self.uri.rstrip("/")

        # Add remaining parts, strip leading/trailing slashes
        part = part.strip("/")
        if part:
            result = f"{result}/{part}"

        return VikingURI(result)

    @staticmethod
    def build(scope: str, *path_parts: str) -> str:
        """
        Build a Viking URI from components.

        Args:
            scope: Scope (resources, user, agent, session, queue)
            *path_parts: Additional path components

        Returns:
            Viking URI string
        """
        if scope not in VikingURI.VALID_SCOPES:
            raise ValueError(f"Invalid scope '{scope}'. Must be one of {VikingURI.VALID_SCOPES}")

        parts = [scope] + list(path_parts)
        # Filter out empty parts
        parts = [p for p in parts if p]
        return f"{VikingURI.SCHEME}://{'/'.join(parts)}"

    @staticmethod
    def build_semantic_uri(
        parent_uri: str,
        semantic_name: str,
        node_id: Optional[str] = None,
        is_leaf: bool = False,
    ) -> str:
        """
        Build a semantic URI based on parent URI.
        """
        # Sanitize semantic name for URI
        safe_name = VikingURI.sanitize_segment(semantic_name)

        if not is_leaf:
            return f"{parent_uri}/{safe_name}"
        else:
            if not node_id:
                raise ValueError("Leaf node must have a node_id")
            return f"{parent_uri}/{safe_name}/{node_id}"

    @staticmethod
    def sanitize_segment(text: str) -> str:
        """
        Sanitize text for use in URI segment.

        Preserves CJK characters (Chinese, Japanese, Korean) and other common scripts
        while replacing special characters.

        Args:
            text: Original text

        Returns:
            URI-safe string
        """
        # Preserve:
        # - Letters, numbers, underscores, hyphens (\w includes [a-zA-Z0-9_])
        # - CJK Unified Ideographs (Chinese, Japanese Kanji, Korean Hanja)
        # - Hiragana and Katakana (Japanese)
        # - Hangul Syllables (Korean)
        # - CJK Unified Ideographs Extension A
        # - CJK Unified Ideographs Extension B
        safe = re.sub(
            r"[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u3400-\u4dbf\U00020000-\U0002a6df\-]",
            "_",
            text,
        )
        # Merge consecutive underscores
        safe = re.sub(r"_+", "_", safe)
        # Strip leading/trailing underscores and limit length
        safe = safe.strip("_")[:50]
        return safe or "unnamed"

    def __str__(self) -> str:
        return self.uri

    def __repr__(self) -> str:
        return f"VikingURI('{self.uri}')"

    def __eq__(self, other) -> bool:
        if isinstance(other, VikingURI):
            return self.uri == other.uri
        return self.uri == str(other)

    def __hash__(self) -> int:
        return hash(self.uri)

    @staticmethod
    def normalize(uri: str) -> str:
        """
        Normalize URI by ensuring it has the viking:// scheme.

        If the input already starts with viking://, returns it as-is.
        If it starts with /, prepends viking:// (resulting in viking:///... which is invalid,
        so we strip leading / first).
        Otherwise, prepends viking://.

        Examples:
            "/resources/images" -> "viking://resources/images"
            "resources/images" -> "viking://resources/images"
            "viking://resources/images" -> "viking://resources/images"

        Args:
            uri: Input URI string

        Returns:
            Normalized URI with viking:// scheme
        """
        if uri.startswith(f"{VikingURI.SCHEME}://"):
            return uri
        # Strip leading slashes
        uri = uri.lstrip("/")
        return f"{VikingURI.SCHEME}://{uri}"

    @classmethod
    def create_temp_uri(cls) -> str:
        """Create temp directory URI like viking://temp/MMDDHHMM_XXXXXX"""
        import datetime
        import uuid

        temp_id = uuid.uuid4().hex[:6]
        return f"viking://temp/{datetime.datetime.now().strftime('%m%d%H%M')}_{temp_id}"
