# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
BaseObserver: Abstract base class for storage system observers.

Defines the common interface that all observers must implement.
"""

import abc


class BaseObserver(abc.ABC):
    """
    BaseObserver: Abstract base class for storage system observers.

    All observer implementations should inherit from this class and implement
    required methods for monitoring and reporting system status.
    """

    @abc.abstractmethod
    def get_status_table(self) -> str:
        """
        Format status information as a string.

        Returns:
            Formatted table string representation of status information
        """
        pass

    @abc.abstractmethod
    def is_healthy(self) -> bool:
        """
        Check if the observed system is healthy.

        Returns:
            True: if system is healthy, False otherwise
        """
        pass

    @abc.abstractmethod
    def has_errors(self) -> bool:
        """
        Check if the observed system has any errors.

        Returns:
            True: if errors exist, False otherwise
        """
        pass
