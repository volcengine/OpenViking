# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Authentication health check module for OIDC and LDAP auth modes.

This module provides startup self-check capabilities to validate authentication
configurations are working before the server starts accepting traffic.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from openviking.server.config import ServerConfig

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Health check status."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    name: str
    status: HealthStatus
    message: str
    error: Optional[Exception] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "error": str(self.error) if self.error else None,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class HealthCheckReport:
    """Complete health check report."""
    overall_status: HealthStatus
    checks: List[HealthCheckResult]
    auth_mode: str
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "auth_mode": self.auth_mode,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
        }


class AuthHealthChecker(ABC):
    """Abstract base class for auth mode health checkers."""

    @abstractmethod
    def name(self) -> str:
        """Name of this checker."""
        pass

    @abstractmethod
    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        """Run all health checks for this auth mode."""
        pass


class OIDCAuthChecker(AuthHealthChecker):
    """Health checker for OIDC auth mode."""

    def name(self) -> str:
        return "oidc"

    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        from time import time
        results: List[HealthCheckResult] = []

        if config.oidc is None:
            results.append(HealthCheckResult(
                name="OIDC config present",
                status=HealthStatus.FAIL,
                message="OIDC config missing, please add server.oidc to your ov.conf",
            ))
            return results

        # Check 1: Issuer is present
        start_time = time()
        if not config.oidc.issuer:
            results.append(HealthCheckResult(
                name="OIDC Issuer configured",
                status=HealthStatus.FAIL,
                message="OIDC issuer not configured, please set server.oidc.issuer",
                duration_seconds=time() - start_time,
            ))
        else:
            results.append(HealthCheckResult(
                name="OIDC Issuer configured",
                status=HealthStatus.PASS,
                message=f"Issuer configured successfully: {config.oidc.issuer}",
                duration_seconds=time() - start_time,
            ))

        # Check 2: JWKS endpoint is accessible via OIDC Discovery.
        # Per OpenID Connect Discovery 1.0 we must fetch
        # {issuer}/.well-known/openid-configuration and use the returned
        # jwks_uri, not assume a hardcoded /.well-known/jwks.json path.
        start_time = time()
        try:
            import httpx

            if config.oidc.jwks_uri:
                jwks_uri = config.oidc.jwks_uri
            else:
                discovery_url = (
                    f"{config.oidc.issuer.rstrip('/')}/.well-known/openid-configuration"
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    disc_response = await client.get(discovery_url)
                    disc_response.raise_for_status()
                    metadata = disc_response.json()
                jwks_uri = metadata.get("jwks_uri")
                if not jwks_uri:
                    raise RuntimeError(
                        f"OIDC discovery document at {discovery_url} "
                        "did not contain a jwks_uri"
                    )

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(jwks_uri)
                response.raise_for_status()
                jwks = response.json()
                keys = jwks.get("keys", [])
                if not keys:
                    results.append(HealthCheckResult(
                        name="OIDC provider connection",
                        status=HealthStatus.WARN,
                        message="Connected to OIDC provider but no signing keys found",
                        duration_seconds=time() - start_time,
                    ))
                else:
                    results.append(HealthCheckResult(
                        name="OIDC provider connection",
                        status=HealthStatus.PASS,
                        message=(
                            f"Successfully connected to OIDC provider via {jwks_uri}, "
                            f"found {len(keys)} signing keys"
                        ),
                        duration_seconds=time() - start_time,
                    ))
        except Exception as e:
            results.append(HealthCheckResult(
                name="OIDC provider connection",
                status=HealthStatus.WARN,
                message=f"Could not connect to OIDC provider: {str(e)[:200]}",
                error=e,
                duration_seconds=time() - start_time,
            ))

        # Check 3: Identity mapping defaults are reasonable
        start_time = time()
        try:
            from openviking.server.auth.identity_mapping import IdentityMapper
            IdentityMapper(config.oidc.identity)
            results.append(HealthCheckResult(
                name="OIDC identity mapping config",
                status=HealthStatus.PASS,
                message="Identity mapping configuration is valid",
                duration_seconds=time() - start_time,
            ))
        except Exception as e:
            results.append(HealthCheckResult(
                name="OIDC identity mapping config",
                status=HealthStatus.WARN,
                message=f"Identity mapping configuration has an issue: {str(e)}",
                error=e,
                duration_seconds=time() - start_time,
            ))

        return results


class LDAPAuthChecker(AuthHealthChecker):
    """Health checker for LDAP auth mode."""

    def name(self) -> str:
        return "ldap"

    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        from time import time
        results: List[HealthCheckResult] = []

        if config.ldap is None:
            results.append(HealthCheckResult(
                name="ldap_config_present",
                status=HealthStatus.FAIL,
                message="LDAP config is missing",
            ))
            return results

        # Check 1: Host is configured
        start_time = time()
        if not config.ldap.host:
            results.append(HealthCheckResult(
                name="ldap_host_configured",
                status=HealthStatus.FAIL,
                message="LDAP host is not configured",
                duration_seconds=time() - start_time,
            ))
        else:
            results.append(HealthCheckResult(
                name="ldap_host_configured",
                status=HealthStatus.PASS,
                message=f"LDAP host configured: {config.ldap.host}:{config.ldap.port}",
                duration_seconds=time() - start_time,
            ))

        # Check 2: Can connect and bind
        start_time = time()
        try:
            import ldap
            protocol = "ldaps" if config.ldap.use_ssl else "ldap"
            ldap_url = f"{protocol}://{config.ldap.host}:{config.ldap.port}"

            conn = ldap.initialize(ldap_url)
            conn.set_option(ldap.OPT_REFERRALS, 0)
            conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
            conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)
            conn.set_option(ldap.OPT_TIMEOUT, 10)

            if config.ldap.use_starttls and not config.ldap.use_ssl:
                conn.start_tls_s()

            if config.ldap.bind_dn and config.ldap.bind_password:
                conn.simple_bind_s(config.ldap.bind_dn, config.ldap.bind_password)
                results.append(HealthCheckResult(
                    name="ldap_connection",
                    status=HealthStatus.PASS,
                    message=f"Successfully connected and bound as {config.ldap.bind_dn}",
                    duration_seconds=time() - start_time,
                ))
            else:
                # Just test anonymous bind if no bind dn configured
                conn.simple_bind_s("", "")
                results.append(HealthCheckResult(
                    name="ldap_connection",
                    status=HealthStatus.PASS,
                    message="Successfully connected anonymously (no bind dn configured)",
                    duration_seconds=time() - start_time,
                ))

            try:
                conn.unbind()
            except Exception:
                pass

        except ImportError:
            results.append(HealthCheckResult(
                name="ldap_connection",
                status=HealthStatus.FAIL,
                message="python-ldap not installed, cannot test connection",
                duration_seconds=time() - start_time,
            ))
        except Exception as e:
            results.append(HealthCheckResult(
                name="ldap_connection",
                status=HealthStatus.WARN,
                message=f"Failed to connect to LDAP: {str(e)[:200]}",
                error=e,
                duration_seconds=time() - start_time,
            ))

        # Check 3: Base DN is configured
        start_time = time()
        if config.ldap.base_dn:
            results.append(HealthCheckResult(
                name="ldap_base_dn_configured",
                status=HealthStatus.PASS,
                message=f"Base DN configured: {config.ldap.base_dn}",
                duration_seconds=time() - start_time,
            ))
        else:
            results.append(HealthCheckResult(
                name="ldap_base_dn_configured",
                status=HealthStatus.FAIL,
                message="Base DN is not configured",
                duration_seconds=time() - start_time,
            ))

        # Check 4: Identity mapping is valid
        start_time = time()
        try:
            from openviking.server.auth.identity_mapping import IdentityMapper
            # Just initialize the mapper
            IdentityMapper(config.ldap.identity)
            results.append(HealthCheckResult(
                name="ldap_mapping_config_valid",
                status=HealthStatus.PASS,
                message="Identity mapping configuration is valid",
                duration_seconds=time() - start_time,
            ))
        except Exception as e:
            results.append(HealthCheckResult(
                name="ldap_mapping_config_valid",
                status=HealthStatus.WARN,
                message=f"Identity mapping configuration check failed: {str(e)}",
                error=e,
                duration_seconds=time() - start_time,
            ))

        return results


class ApiKeyAuthChecker(AuthHealthChecker):
    """Health checker for API key auth mode."""

    def name(self) -> str:
        return "api_key"

    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        from time import time
        results: List[HealthCheckResult] = []

        # Check: Root API key is configured
        start_time = time()
        if config.root_api_key:
            results.append(HealthCheckResult(
                name="api_key_root_key_configured",
                status=HealthStatus.PASS,
                message="Root API key is configured",
                duration_seconds=time() - start_time,
            ))
        else:
            results.append(HealthCheckResult(
                name="api_key_root_key_configured",
                status=HealthStatus.WARN,
                message="Root API key is not configured (will auto-select dev mode)",
                duration_seconds=time() - start_time,
            ))

        return results


class TrustedAuthChecker(AuthHealthChecker):
    """Health checker for trusted auth mode."""

    def name(self) -> str:
        return "trusted"

    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        from time import time
        results: List[HealthCheckResult] = []

        # Basic config check
        start_time = time()
        results.append(HealthCheckResult(
            name="trusted_config_valid",
            status=HealthStatus.PASS,
            message="Trusted mode configuration is valid",
            duration_seconds=time() - start_time,
        ))

        return results


class DevAuthChecker(AuthHealthChecker):
    """Health checker for dev auth mode."""

    def name(self) -> str:
        return "dev"

    async def run_checks(self, config: ServerConfig) -> List[HealthCheckResult]:
        from time import time
        results: List[HealthCheckResult] = []

        # Check host is localhost
        start_time = time()
        if config.host in ["localhost", "127.0.0.1", "::1"]:
            results.append(HealthCheckResult(
                name="dev_host_local",
                status=HealthStatus.PASS,
                message=f"Host is set to loopback: {config.host}",
                duration_seconds=time() - start_time,
            ))
        else:
            results.append(HealthCheckResult(
                name="dev_host_local",
                status=HealthStatus.WARN,
                message=f"Dev mode should be used with localhost only, current host: {config.host}",
                duration_seconds=time() - start_time,
            ))

        return results


# Registry of health checkers
_AUTH_CHECKERS: Dict[str, AuthHealthChecker] = {}


def register_checker(auth_mode: str, checker: AuthHealthChecker) -> None:
    """Register an auth health checker for a mode."""
    _AUTH_CHECKERS[auth_mode] = checker


def get_checker(auth_mode: str) -> Optional[AuthHealthChecker]:
    """Get the health checker for an auth mode."""
    return _AUTH_CHECKERS.get(auth_mode)


# Register default checkers
register_checker("oidc", OIDCAuthChecker())
register_checker("ldap", LDAPAuthChecker())
register_checker("api_key", ApiKeyAuthChecker())
register_checker("trusted", TrustedAuthChecker())
register_checker("dev", DevAuthChecker())


async def run_health_check(config: ServerConfig, fail_on_warn: bool = False) -> HealthCheckReport:
    """Run health checks for the configured auth mode.

    Args:
        config: Server configuration.
        fail_on_warn: If True, treat WARN as FAIL for overall status.

    Returns:
        HealthCheckReport with the results.
    """
    auth_mode = config.get_effective_auth_mode()
    checker = get_checker(auth_mode)

    if not checker:
        logger.warning("No health checker registered for auth mode: %s", auth_mode)
        return HealthCheckReport(
            overall_status=HealthStatus.PASS,
            checks=[HealthCheckResult(
                name="no_checker",
                status=HealthStatus.PASS,
                message=f"No health checker available for mode: {auth_mode}",
            )],
            auth_mode=auth_mode,
            summary=f"No health checks available for auth mode: {auth_mode}",
        )

    checks = await checker.run_checks(config)

    # Determine overall status
    has_fail = any(c.status == HealthStatus.FAIL for c in checks)
    has_warn = any(c.status == HealthStatus.WARN for c in checks)

    if has_fail:
        overall_status = HealthStatus.FAIL
    elif has_warn and fail_on_warn:
        overall_status = HealthStatus.FAIL
    elif has_warn:
        overall_status = HealthStatus.WARN
    else:
        overall_status = HealthStatus.PASS

    # Generate summary
    fail_count = sum(1 for c in checks if c.status == HealthStatus.FAIL)
    warn_count = sum(1 for c in checks if c.status == HealthStatus.WARN)
    pass_count = sum(1 for c in checks if c.status == HealthStatus.PASS)

    summary = f"Health check: {pass_count} passed, {warn_count} warnings, {fail_count} failed"

    report = HealthCheckReport(
        overall_status=overall_status,
        checks=checks,
        auth_mode=auth_mode,
        summary=summary,
    )

    # Log results
    logger.info("=" * 60)
    logger.info("AUTHENTICATION HEALTH CHECK")
    logger.info("=" * 60)
    logger.info("Auth mode: %s", auth_mode)
    logger.info("Overall: %s", overall_status.value.upper())
    logger.info("-" * 60)

    for check in checks:
        icon = {
            HealthStatus.PASS: "✅",
            HealthStatus.WARN: "⚠️",
            HealthStatus.FAIL: "❌",
        }.get(check.status, "?")
        log_func = {
            HealthStatus.PASS: logger.info,
            HealthStatus.WARN: logger.warning,
            HealthStatus.FAIL: logger.error,
        }.get(check.status, logger.info)

        duration_str = f" ({check.duration_seconds:.2f}s)" if check.duration_seconds else ""
        log_func("%s %s: %s%s", icon, check.name, check.message, duration_str)

        if check.error:
            logger.debug("Error detail: %r", check.error)

    logger.info("-" * 60)
    logger.info(summary)
    logger.info("=" * 60)

    return report


async def run_startup_health_check_or_exit(config: ServerConfig) -> None:
    """Run health check and exit with error if critical checks fail.

    This function is used during server bootstrap to ensure authentication
    configuration is valid before starting the server.
    """
    auth_mode = config.get_effective_auth_mode()
    logger.info(f"[AUTH] Running authentication health check for auth_mode={auth_mode}")
    report = await run_health_check(config, fail_on_warn=False)

    for check in report.checks:
        if check.status == HealthStatus.PASS:
            logger.info(f"[AUTH] {check.name}: PASS - {check.message}")
        elif check.status == HealthStatus.WARN:
            logger.warning(f"[AUTH] {check.name}: WARN - {check.message}")
        else:
            logger.error(f"[AUTH] {check.name}: FAIL - {check.message}")

    if report.overall_status == HealthStatus.FAIL:
        logger.error("[AUTH] Authentication health check failed, aborting startup")
        print("\n" + "=" * 60, file=sys.stderr)
        print("FATAL: AUTHENTICATION HEALTH CHECK FAILED", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for check in report.checks:
            if check.status == HealthStatus.FAIL:
                print(f"  ❌ {check.name}: {check.message}", file=sys.stderr)
        print("\nPlease fix the configuration issues above and try again.", file=sys.stderr)
        sys.exit(1)
    elif report.overall_status == HealthStatus.WARN:
        logger.warning("[AUTH] Authentication health check passed with warnings")
    else:
        logger.info("[AUTH] Authentication health check passed")
