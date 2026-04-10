# Web Studio Agent Notes

This file defines implementation rules for the web-studio frontend workspace. Follow these rules by default when editing code in this directory.

## Scope

- Applies to the entire web-studio workspace.
- This is a Vite-based React 19 SPA using TanStack Router file routing.
- The current work is still in a scaffold-first phase for the main product areas.

## Current Placeholder Status

- The current placeholder page list is:
  - src/routes/resources/route.tsx
  - src/routes/sessions/route.tsx
  - src/routes/operations/route.tsx
  - src/routes/admin/route.tsx
- The shared placeholder infrastructure currently centers on src/components/placeholder-page.tsx.
- A route that still renders PlaceholderPage should normally be treated as a placeholder page unless there is an explicit exception documented in the same change.
- The related translation entries in src/i18n/resources.ts for those areas are also placeholder copy that describes future implementation intent.
- When a feature is actually implemented, remove or rewrite placeholder notes, placeholder descriptions, and “will be added later” style copy in both the route UI and the translation resources.
- Do not keep outdated placeholder messaging after real functionality exists.
- Treat the list above as a maintained inventory, not a one-time note.
- If an agent adds a new placeholder page, it must add that route file to this list in the same change.
- If an agent replaces a placeholder page with a real implementation, it must remove that route file from this list in the same change.
- When removing an item from the placeholder page list, also clean the matching placeholder translations in src/i18n/resources.ts.
- When a page no longer needs PlaceholderPage, replace the placeholder layout instead of wrapping real functionality inside the placeholder scaffold.

## Routing Rules

- Define route entry files only under src/routes.
- Use directory routes for top-level pages: src/routes/<page>/route.tsx.
- Keep page-private implementation inside the corresponding route directory.
- Prefix page-private folders with - so TanStack Router ignores them.
- Do not move page-private components, hooks, schemas, or helpers back into broad shared directories unless they are truly reusable.

Recommended page-private folders:

- src/routes/<page>/-components
- src/routes/<page>/-hooks
- src/routes/<page>/-lib
- src/routes/<page>/-constants
- src/routes/<page>/-schemas
- src/routes/<page>/-types

## i18n Rules

- All user-visible copy must live in src/i18n/resources.ts.
- React components must use useTranslation instead of inline UI strings.
- Config objects must store keys, not final display text.
- If hooks, lib helpers, selectors, or formatters feed content into the UI, return keys or key-plus-values objects instead of translated strings.
- Keep shared actions, statuses, and generic placeholder text in the common namespace.
- Keep page-specific copy in the relevant namespace, such as appShell, connection, resources, sessions, operations, or admin.
- Keep key depth shallow, normally 2 to 3 levels, and organize by meaning rather than DOM hierarchy.

Preferred pattern:

```ts
type Summary = {
  labelKey: 'identitySummary.named' | 'identitySummary.unset'
  values?: { identity?: string }
}
```

Avoid:

```ts
return 'Identity not set'
return '服务端隐式身份'
```

## UI and Component Boundaries

- src/components/ui is only for reusable base UI primitives.
- Page-specific UI should be colocated under the relevant route directory.
- Top-level route files should stay focused on page composition, translation binding, and high-level data orchestration.
- Rendering layers call t(); utility layers should not assemble final UI copy.
- When replacing placeholder pages with real implementation, also remove placeholder cards, placeholder labels, and placeholder translation entries that no longer describe the real product.
- Treat src/components/placeholder-page.tsx as scaffold infrastructure for unfinished pages, not as a permanent application layout primitive.

## API Client Rules

- Application code should import from #/lib/ov-client by default.
- Never hand-edit src/gen/ov-client.
- Keep auth header injection, telemetry injection, and error normalization inside src/lib/ov-client.
- Do not reintroduce old console BFF semantics or compatibility aliases such as /console/api/v1 or /ov/....
- Keep using these request headers unless there is an explicit migration:
  - X-API-Key
  - X-OpenViking-Account
  - X-OpenViking-User
  - X-OpenViking-Agent

## Lint and Generated Files

- The current lint scope is intentionally focused on business code under src.
- The current excluded paths are src/gen, src/routeTree.gen.ts, and src/components/ui.
- Generated files must only be updated through generation commands, not manual edits.

## Validation Checklist

- When adding user-visible copy, update both zh-CN and en resources.
- When adding a new page, prefer a directory route and page-private subfolders.
- When adding config-driven UI copy, use titleKey, descriptionKey, labelKey, or similar key fields.
- After normal business-code changes, run npm run lint.
- After routing, build, or client integration changes, also run npm run build.
- When converting a placeholder page into a real feature, remove the placeholder wording from both the UI and src/i18n/resources.ts as part of the same change.
- Keep the placeholder page list in the Current Placeholder Status section accurate after every route-level feature change.
- If a route stops using src/components/placeholder-page.tsx, verify whether it should also be removed from the placeholder page list.