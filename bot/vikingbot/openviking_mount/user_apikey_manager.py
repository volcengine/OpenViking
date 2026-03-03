"""User API Key persistence manager for OpenViking remote mode."""

import json
import hashlib
from pathlib import Path
from typing import Optional

from loguru import logger


class UserApiKeyManager:
    """Manages user API key persistence based on server_url and account_id.

    Stores API keys in a JSON file located at:
    {ov_path}/user_apikeys_{hash}.json

    where {hash} is derived from server_url and account_id.
    """

    def __init__(self, ov_path: Path, server_url: str, account_id: str):
        """Initialize the API key manager.

        Args:
            ov_path: The ov_path where config files are stored
            server_url: The OpenViking server URL
            account_id: The account ID
        """
        self.ov_path = Path(ov_path)
        self.server_url = server_url
        self.account_id = account_id

        # Generate hash from server_url and account_id
        hash_input = f"{server_url}:{account_id}"
        self.config_hash = hashlib.md5(hash_input.encode()).hexdigest()[:16]

        # Config file path
        self.config_dir = self.ov_path
        self.config_file = self.config_dir / f"user_apikeys_{self.config_hash}.json"

        # In-memory cache
        self._apikeys: dict[str, str] = {}
        self._loaded = False

    def _ensure_config_dir(self) -> None:
        """Ensure the config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        """Load API keys from the config file."""
        if self._loaded:
            return

        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._apikeys = data.get("apikeys", {})
            except Exception as e:
                logger.warning(f"Failed to load API keys from {self.config_file}: {e}")
                self._apikeys = {}
        else:
            logger.debug(f"API key config file not found: {self.config_file}")

        self._loaded = True

    def _save(self) -> None:
        """Save API keys to the config file."""
        self._ensure_config_dir()

        try:
            data = {
                "server_url": self.server_url,
                "account_id": self.account_id,
                "apikeys": self._apikeys,
            }

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(f"Saved {len(self._apikeys)} API keys to {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to save API keys to {self.config_file}: {e}")
            raise

    def get_apikey(self, user_id: str) -> Optional[str]:
        """Get the API key for a specific user.

        Args:
            user_id: The user ID

        Returns:
            The API key if found, None otherwise
        """
        self._load()
        return self._apikeys.get(user_id)

    def set_apikey(self, user_id: str, api_key: str) -> None:
        """Set the API key for a specific user.

        Args:
            user_id: The user ID
            api_key: The API key to store
        """
        self._load()
        self._apikeys[user_id] = api_key
        self._save()

    def delete_apikey(self, user_id: str) -> bool:
        """Delete the API key for a specific user.

        Args:
            user_id: The user ID

        Returns:
            True if the key was deleted, False if not found
        """
        self._load()
        if user_id in self._apikeys:
            del self._apikeys[user_id]
            self._save()
            return True
        return False
