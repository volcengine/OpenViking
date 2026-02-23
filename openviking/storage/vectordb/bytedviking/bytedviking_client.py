# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import importlib
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class BytedVikingClient:
    """
    Base class for Byted Viking client implementations using reflection to avoid direct dependency
    on any specific viking client implementation.
    """

    def __init__(self, collection_name: str, config: Dict[str, Any]):
        """
        Initialize BytedVikingClient with configuration.
        """
        self.collection_name = collection_name
        self.config = config
        
        # 1. Dynamic Import
        package_name = config.get("package_name", "viking.vikingdb_client")
        try:
            self._module = importlib.import_module(package_name)
        except ImportError as e:
            raise ImportError(f"Failed to import viking client module '{package_name}': {e}")

        # 2. Get Classes via Reflection
        try:
            self._MetaClientClass = getattr(self._module, "VikingDbMetaClient")
            self._DataClientClass = getattr(self._module, "VikingDbClient")
            # We might need helper classes like VikingDbData if we need to construct them
            self._VikingDbDataClass = getattr(self._module, "VikingDbData")
        except AttributeError as e:
            raise AttributeError(f"Failed to get required classes from '{package_name}': {e}")

        # 3. Initialize Meta Client
        self.region = config.get("region", "CN")
        self.byterec_domain = config.get("domain", "byterec.bytedance.net")
        self.db_token = config.get("db_token", 'null')
        self.namespace = config.get("namespace", "null")
        self.vikingdb_name = config.get("db_name", "null")
        self.caller_name = config.get("caller_name", "null")
        self.caller_key = config.get("caller_key", "null")

        self.meta_client = self._MetaClientClass(
            byterec_domain=self.byterec_domain,
            region=self.region,
            namespace=self.namespace,
            caller_name=self.caller_name,
            caller_key=self.caller_key
        )
        
        self.db_client = self._DataClientClass(
            vikingdb_name=self.vikingdb_name,
            token=self.db_token,
            region=self.region,

            # Pass other optional configs if needed
            pool_connections=config.get("pool_connections", 10),
            pool_maxsize=config.get("pool_maxsize", 10)
        )