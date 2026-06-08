# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Common exceptions for credential failover functionality."""


class AllCredentialsFailedError(Exception):
    """Raised when all credentials in the chain have failed."""

    def __init__(self, errors: list[tuple[str, str, Exception, int]]):
        """Initialize the error with a list of credential failures.

        Args:
            errors: List of tuples containing (credential_id, error_class, exception, attempts)
        """
        self.errors = errors
        message = "All credentials failed:\n" + "\n".join(
            f"  - {cred_id}: {error_class} - {exc}"
            for cred_id, error_class, exc, attempts in errors
        )
        super().__init__(message)
