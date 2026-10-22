# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Any, get_args, get_origin

from fastapi.routing import APIRoute
from pydantic import BaseModel

OPEN_BODY_MODEL_EXCEPTIONS = {
    ("POST", "/api/v1/admin/accounts", "CreateAccountRequest.user_config.memory_policy"):
        "User memory policy is an extensible policy document.",
    ("POST", "/api/v1/admin/accounts", "CreateAccountRequest.settings"):
        "Account settings are validated by the runtime config manager.",
    (
        "PATCH",
        "/api/v1/admin/accounts/{account_id}/configuration",
        "ConfigPatchRequest.settings",
    ): "Account settings are validated by the runtime config manager.",
    (
        "POST",
        "/api/v1/admin/configuration/test-model",
        "ModelConnectionTestRequest.config",
    ): "Model provider configuration is provider-specific.",
    (
        "POST",
        "/api/v1/admin/configuration/test-model",
        "ModelConnectionTestRequest.fallback",
    ): "Fallback model configuration is provider-specific.",
    ("PATCH", "/api/v1/admin/configuration", "ConfigPatchRequest.settings"):
        "Cluster settings are validated by the runtime config manager.",
    (
        "POST",
        "/api/v1/admin/accounts/{account_id}/users",
        "RegisterUserRequest.user_config.memory_policy",
    ): "User memory policy is an extensible policy document.",
    ("POST", "/api/v1/compile", "CompileRequest.args"):
        "Compiler arguments are implementation-specific.",
    ("POST", "/api/v1/resources", "AddResourceRequest.args"):
        "Resource parser arguments are implementation-specific.",
    ("POST", "/api/v1/skills", "AddSkillRequest.data"):
        "Skill input accepts structured or binary-compatible payloads.",
    ("POST", "/api/v1/skills", "AddSkillRequest.source_metadata"):
        "Imported skill metadata is source-specific.",
    ("POST", "/api/v1/search/find", "FindRequest.filter"):
        "Vector filters use an extensible expression document.",
    ("POST", "/api/v1/search/search", "SearchRequest.filter"):
        "Vector filters use an extensible expression document.",
    (
        "POST",
        "/api/v1/privacy-configs/{category}/{target_key}",
        "UpsertPrivacyConfigRequest.values",
    ): "Privacy values depend on the selected category.",
    (
        "POST",
        "/api/v1/privacy-configs/{category}/{target_key}",
        "UpsertPrivacyConfigRequest.labels",
    ): "Privacy labels are extensible metadata.",
    (
        "PATCH",
        "/api/v1/privacy-configs/{category}/{target_key}",
        "PatchPrivacyConfigRequest.values",
    ): "Privacy values depend on the selected category.",
    (
        "PATCH",
        "/api/v1/privacy-configs/{category}/{target_key}",
        "PatchPrivacyConfigRequest.labels",
    ): "Privacy labels are extensible metadata.",
    ("POST", "/api/v1/skills/validate", "ValidateSkillRequest.data"):
        "Skill validation accepts multiple source payload shapes.",
    ("PUT", "/api/v1/skills/{skill_name}", "UpdateSkillRequest.data"):
        "Skill input accepts structured or binary-compatible payloads.",
    ("PUT", "/api/v1/skills/{skill_name}", "UpdateSkillRequest.source_metadata"):
        "Imported skill metadata is source-specific.",
    ("POST", "/api/v1/sessions", "CreateSessionRequest.memory_policy"):
        "Session memory policy is an extensible policy document.",
    (
        "POST",
        "/api/v1/sessions/{session_id}/messages",
        "AddMessageRequest.parts",
    ): "Message parts support provider-defined part payloads.",
    (
        "POST",
        "/api/v1/sessions/{session_id}/messages/batch",
        "BatchAddMessageRequest.messages.parts",
    ): "Message parts support provider-defined part payloads.",
}


def _contains_open_payload(annotation: object) -> bool:
    if annotation is Any:
        return True
    origin = get_origin(annotation)
    if origin is dict:
        args = get_args(annotation)
        return not args or _contains_open_payload(args[-1])
    if origin is not None:
        return any(
            _contains_open_payload(arg)
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    return False


def _walk_body_models(
    annotation: object,
    field_path: str | None = None,
    seen: set[tuple[type[BaseModel], str | None]] | None = None,
):
    if seen is None:
        seen = set()
    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            if arg is not type(None):
                yield from _walk_body_models(arg, field_path, seen)
        return
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        return

    path = field_path or annotation.__name__
    marker = (annotation, path)
    if marker in seen:
        return
    seen.add(marker)
    yield annotation, path, False
    for name, field in annotation.model_fields.items():
        nested_path = f"{path}.{name}"
        if _contains_open_payload(field.annotation):
            yield annotation, nested_path, True
        yield from _walk_body_models(field.annotation, nested_path, seen)


async def test_all_http_body_models_have_explicit_closed_or_open_policy(app):
    discovered_exceptions = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        for body_param in route.dependant.body_params:
            annotation = body_param.field_info.annotation
            for method in route.methods:
                for model, field_path, is_open in _walk_body_models(annotation):
                    if is_open:
                        discovered_exceptions.add((method, route.path, field_path))
                    else:
                        assert model.model_config.get("extra") == "forbid", (
                            f"{method} {route.path} uses open fixed model "
                            f"{model.__module__}.{model.__name__}"
                        )

    assert discovered_exceptions == set(OPEN_BODY_MODEL_EXCEPTIONS)
