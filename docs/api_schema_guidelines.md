# API Response Typing Guidelines

This document defines the contract all OpenViking HTTP routers MUST follow so
that the generated OpenAPI schema is complete, and client SDKs (TypeScript,
Python, etc.) can be generated with real types instead of `any`.

## 1. Every JSON endpoint MUST declare its response type

Both the decorator's `response_model` AND the function's return type annotation
must be provided. Keeping them in sync is enforced by review.

```python
# GOOD
@router.get("/sessions", response_model=Response[PaginatedResult[SessionInfo]])
async def list_sessions(...) -> Response[PaginatedResult[SessionInfo]]:
    return Response(
        status="ok",
        result=PaginatedResult(items=[...], pagination=Pagination(...)),
    )

# BAD — no typing at all
@router.get("/sessions")
async def list_sessions(...):
    return {"items": [...]}

# BAD — decorator has type but function returns raw dict
@router.get("/sessions", response_model=Response[PaginatedResult[SessionInfo]])
async def list_sessions(...):
    return {"status": "ok", "result": {"items": [...]}}
```

## 2. Return model instances — never `.model_dump()`

Inside a route handler, `return` must produce a Pydantic model instance.
FastAPI applies `response_model` to serialize it. Calling `.model_dump(...)`
yourself bypasses the typed contract and produces inconsistent null behavior.

```python
# GOOD
return Response(status="ok", result=SessionDetail(...))

# BAD
return Response(status="ok", result=SessionDetail(...)).model_dump(exclude_none=True)
```

**Exemption boundary** — the rule applies **only** to values that a route
handler `return`s:

- **Route return values**: MUST NOT be `.model_dump()` output. Always return
  the model instance and let FastAPI serialize via `response_model`.
- **Global exception handlers** in `openviking/server/app.py`: MAY use
  `JSONResponse(..., content=Response(...).model_dump())` because exception
  handlers bypass `response_model`. This is the only sanctioned place for a
  route-equivalent code path to use `.model_dump()`.
- **Non-route internal logic** (middleware, background tasks, hook
  callbacks, log payload builders, etc.): unrestricted. Use `.model_dump()`
  freely when you need a `dict` — the route-return rule does not apply.

## 3. Null policy: omit, don't serialize as `null`

Routers SHOULD use `APIRouter(route_class=ExcludeNoneRoute)` so that
`response_model_exclude_none=True` is the default for every endpoint in that
router. This produces JSON with absent keys instead of `"key": null`, matching
the behavior of endpoints that previously used `.model_dump(exclude_none=True)`.

Adoption is **per-router and per-PR**, not global. A business PR that types
module X:

1. Adds `route_class=ExcludeNoneRoute` to that module's `APIRouter(...)`.
2. Lists in the PR body which endpoints in that module may see their
   response shape change from `{"k": null}` to `{}` (i.e. endpoints that
   previously returned `Response(...)` directly without `exclude_none`).
3. Endpoints that already used `.model_dump(exclude_none=True)` see no
   change.

PR#0 only introduces the class — it is not applied to any existing router
until the corresponding business PR opts in.

```python
from openviking.server.schemas import ExcludeNoneRoute

router = APIRouter(
    prefix="/api/v1/sessions",
    tags=["sessions"],
    route_class=ExcludeNoneRoute,
)
```

Endpoints that **must** emit explicit `null` keys (rare — e.g. a field
whose absence vs `null` carries different semantic meaning) cannot
override per-endpoint within the same router because `ExcludeNoneRoute`
unconditionally sets the flag. Move such endpoints to a separate router
that does not use `ExcludeNoneRoute`.

## 4. Non-JSON responses are whitelisted

The following endpoints return raw bytes / streams and MUST keep their current
`StreamingResponse` / `FileResponse` implementation without a `response_model`:

- `openviking/server/routers/bot.py`: SSE streaming (`/chat`)
- `openviking/server/routers/content.py`: blob download
- `openviking/server/routers/pack.py`: zip download

New non-JSON endpoints must be added to this list and the reviewer should
confirm the response is genuinely non-JSON (not just "dict-shaped bytes").

## 5. Dynamic / legacy payloads: start loose, tighten later

When a handler currently returns `task.to_dict()`, `_to_jsonable(x)`, or
similarly structured `dict[str, Any]`, the first typed version SHOULD use a
permissive model:

```python
class TaskDetail(BaseModel):
    id: str
    status: str
    # keep untyped for now; narrow in a follow-up PR
    data: Optional[Dict[str, Any]] = None
```

This avoids breaking existing callers while still removing `any` from the top
level of the OpenAPI schema. Tightening internal fields is a mechanical
follow-up done per-feature.

## 6. Naming conventions

- `XxxInfo` — a single resource (e.g. `SessionInfo`, `ResourceInfo`)
- `XxxDetail` — the expanded form of `XxxInfo` with additional fields
- `XxxListItem` — a summarized item used in list endpoints when different from the full form
- `XxxResponse` — the top-level payload for endpoints whose response does not fit `Response[T]` (rare)

Module-specific models live in `openviking/server/schemas/<module>.py`;
primitives reused across modules live in `openviking/server/schemas/common.py`.

### Mapping historical list payloads

Existing list endpoints historically return ad-hoc shapes such as
`{"items": [...], "total": N}`, `{"items": [...]}`, or
`{"results": [...], "offset": o, "limit": l}`. When typing these endpoints,
follow this policy to stay backward-compatible:

- **Use a custom model that mirrors the historical shape.** Do **not**
  force-fit existing payloads into `PaginatedResult[T]` if that would
  move or rename top-level fields (e.g. moving a bare `total` into a
  `pagination` sub-object is a breaking change). `PaginatedResult[T]`
  is provided for **new** endpoints or explicit migrations where both
  client and server can be updated together.
- **Never rename historical fields.** If the endpoint returns `results`
  (not `items`), the PR introduces a module-local model that preserves
  `results`; don't rewrite the shape.
- **Never drop historical fields.** If the endpoint returned
  `{"items": [...], "total": N, "foo": "bar"}`, the typed model must
  include `foo` even if it's documented as deprecated.
- Each list-endpoint PR must document the before/after JSON shape in the
  PR body so reviewers can verify the mapping.

## 7. Backward-compatibility checklist (per business PR)

Before merging a typed-response PR for a module, verify:

- [ ] (MUST) Every endpoint has both `response_model=` and a return type
      annotation.
- [ ] (MUST) No route handler `return` value is produced by `.model_dump()`
      (non-route internal logic is unrestricted).
- [ ] (SHOULD) The router adopts `route_class=ExcludeNoneRoute`. If adopted,
      the PR body lists endpoints whose response may change from
      `{"k": null}` to `{}`.
- [ ] (SHOULD) A JSON fixture test records the response of at least one
      endpoint before and after the change and asserts byte-level equality
      (modulo `null` fields that were previously absent).
- [ ] (MUST) Dynamic `dict[str, Any]` payloads are either typed as models
      or retained as a permissive `Dict[str, Any]` field — no new `Any`
      leaked into the top-level OpenAPI schema.
