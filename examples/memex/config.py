"""
Memex Configuration Management
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# openviking_cli is a bare package in the monorepo root (not pip-installable).
# Add the monorepo root to sys.path so we can import it.
_MONOREPO_ROOT = str(Path(__file__).resolve().parents[2])
if _MONOREPO_ROOT not in sys.path:
    sys.path.insert(0, _MONOREPO_ROOT)

from openviking_cli.utils.config import OpenVikingConfig


@dataclass
class MemexConfig:
    """Memex configuration."""

    # OpenViking settings
    data_path: str = "./memex_data"
    config_path: str = "./ov.conf"

    # LLM settings (for RAG, read from ov.conf)
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # Search settings
    search_top_k: int = 5
    search_score_threshold: float = 0.1

    # Session settings
    session_id: Optional[str] = None

    # Default URIs
    default_resource_uri: str = "viking://resources/"
    default_user_uri: str = "viking://user/"
    default_agent_uri: str = "viking://agent/"

    # Cached OpenViking config
    _ov_config: Optional[OpenVikingConfig] = None

    @classmethod
    def from_env(cls) -> "MemexConfig":
        """Create config from environment variables."""
        return cls(
            data_path=os.getenv("MEMEX_DATA_PATH", "./memex_data"),
            config_path=os.getenv("MEMEX_CONFIG_PATH", "./ov.conf"),
            llm_temperature=float(os.getenv("MEMEX_LLM_TEMPERATURE", "0.7")),
            llm_max_tokens=int(os.getenv("MEMEX_LLM_MAX_TOKENS", "2048")),
            search_top_k=int(os.getenv("MEMEX_SEARCH_TOP_K", "5")),
            search_score_threshold=float(os.getenv("MEMEX_SEARCH_SCORE_THRESHOLD", "0.1")),
        )

    def get_openviking_config(self) -> OpenVikingConfig:
        """Load OpenViking config from ov.conf file."""
        if self._ov_config is not None:
            return self._ov_config

        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}\n"
                "Please copy ov.conf.example to ov.conf and configure your API keys."
            )

        with open(config_file, "r") as f:
            config_dict = json.load(f)

        self._ov_config = OpenVikingConfig.from_dict(config_dict)
        return self._ov_config

    def get_vlm_config(self) -> dict:
        """Get VLM config for RAG recipe."""
        ov_config = self.get_openviking_config()
        backend = ov_config.vlm.backend or getattr(ov_config.vlm, "provider", None) or "openai"
        return {
            "api_base": ov_config.vlm.api_base,
            "api_key": ov_config.vlm.api_key,
            "model": ov_config.vlm.model,
            "backend": backend,
        }
