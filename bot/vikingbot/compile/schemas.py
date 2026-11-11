"""Assignment-specific emit schemas shared by direct calls and tool-using children."""

from __future__ import annotations

from vikingbot.compile.plan import PlanProposal, ReviewDecision
from vikingbot.compile.results import (
    FileResponse,
    RecordResponse,
    RouteBatchResponse,
    RouteResponse,
)


def result_schema(schema, data):
    """Return an emit schema specialized for the supplied result model and assignment.

    data supplies business field descriptions, input IDs and allowed output paths.
    The returned dictionary is independent of the model's schema and other calls.
    """
    # The model receives strict routing instructions; malformed individual decisions
    # remain available to Shuffle so valid neighbours survive a partial response.
    result = (RouteResponse if schema is RouteBatchResponse else schema).model_json_schema()
    if schema in (PlanProposal, ReviewDecision):
        # Planner-facing fields carry their meaning; class docstrings and generated titles do not.
        for definition in [result, *result["$defs"].values()]:
            definition.pop("title", None)
            definition.pop("description", None)
            for property_schema in definition.get("properties", {}).values():
                property_schema.pop("title", None)
        # Explicit plans are required from the model; stored contracts retain parsing defaults.
        if schema is PlanProposal:
            result["properties"]["plan"].pop("default")
            result["required"] = ["contract", "plan"]
        transform = result["$defs"]["Transform"]
        transform["properties"]["output"].pop("default")
        transform["required"] = ["instructions", "output", "execution"]
        properties = result["$defs"]["Contract"]["properties"]
        # Model output uses objects for referenced configurations, omitting unused optional entries.
        for name, definition in (
            ("reduce", "Transform"),
            ("synthesize", "Transform"),
            ("routing", "Routing"),
            ("final_routing", "Routing"),
        ):
            properties[name] = {
                "$ref": f"#/$defs/{definition}",
                "description": properties[name]["description"],
            }
    if schema in (RouteResponse, RouteBatchResponse):
        ids = [item["record"] for item in data["records"]]
        result["properties"]["decisions"].update(minItems=len(ids), maxItems=len(ids))
        result["$defs"]["RouteDecision"]["properties"]["record"]["enum"] = ids
    if issubclass(schema, FileResponse) and data.get("inputs"):
        # Limit file lineage to supplied inputs without requiring exhaustive coverage.
        ids = [item["id"] for item in data["inputs"]]
        inputs = result["$defs"]["FileDraft"]["properties"]["inputs"]
        inputs["items"]["enum"] = ids
        inputs["uniqueItems"] = True
    if schema is FileResponse and "indexes" in data:
        paths = [item["group"]["path"] for item in data["indexes"]]
        result["properties"]["files"].update(minItems=len(paths), maxItems=len(paths))
        result["$defs"]["FileDraft"]["properties"]["path"]["enum"] = paths
    if schema is RecordResponse and "record_fields" in data:
        record = result["$defs"]["RecordDraft"]
        properties = record["properties"]
        record["required"] = ["inputs", "scope"]
        for name, fields in (("payload", data["record_fields"]), ("scope", data["scope_fields"])):
            value = properties[name]["additionalProperties"]
            if name == "scope":
                value = {"type": "string"}
            properties[name] = {
                "type": "object",
                "description": properties[name]["description"],
                "properties": {
                    field: {**value, "description": fields[field]}
                    if isinstance(fields, dict)
                    else dict(value)
                    for field in fields
                },
                "additionalProperties": value,
            }
    return result
