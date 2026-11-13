# Internal shared code

English / [中文](README_CN.md)

Code that several components or blocks reuse, with **no public entry**. Nothing here is exported from the package root or a subpath, or listed in `exports` in `package.json`: a host cannot import it, and it carries no compatibility promise.

Code belongs here when a second real consumer needs it; what a single component uses stays in that component's directory. Shared code never depends on `components/`, `modules/` or a host contract: the dependency runs one way, from them into this directory. A piece that needs business data, routing or a host service belongs in `modules/`. The [Frontend Agent Guide](../../../../AGENTS.md) has the placement rules.

A stylesheet here is imported by the consuming component's own `style.css`, not by `components/style.css`, because no public entry points at this directory.

## Contents

The directories are the source of truth for what exists; each entry below says what its directory is for.

### `primitives/`

General-purpose basic pieces. The caller supplies business content and specific styles through `children` or props.

- `button`: a trimmed Arco button with variants, sizes, icons, a loading state, native events and a ref. It submits a form only when `htmlType="submit"`. Links, button groups and `ConfigProvider` coupling are omitted.
- `input`: a trimmed Arco `Input` with native events, form attributes, a ref, sizes and decorative prefix and suffix, plus a styled `TextArea`. Password toggle, counter, clear button and auto-size are omitted.
- `select`: children-based single and multiple selection with array values, an Input shell, a borderless Simple variant, and custom value and trigger rendering. See [Select](./primitives/select/README.md).
- `modal`: a controlled, centered modal derived from Arco, with transitions, a focus lock and the shared body scroll lock. Its close label lives in its own `locales/`.
- `drawer`: a right-side drawer with transitions, a focus lock and the shared body scroll lock. It receives its content through `children`.
- `tooltip`: a positioned surface rendered into `document.body`, shown on hover or focus, or toggled by click. It renders into the body because an ancestor's `overflow: hidden` would clip an in-flow surface.
- `popover`: the white-card counterpart of `tooltip` for structured or interactive content, also rendered into `document.body`, with the same trigger and placement options.
- `divider`: a single `role="separator"` rule whose length, thickness and color the caller passes.
- `resizable`: `ResizableHandle`, a draggable `role="separator"` that resizes a neighbouring panel with pointer drag or the arrow keys. The caller owns the width and receives each clamped value through `onResize`.
- `overlay`: positioning and scroll helpers for floating surfaces such as `tooltip`, `popover`, `modal` and `drawer`. `useOverlayPosition` flips a surface to the opposite side when space runs out and clamps it to the viewport, `lockBodyScroll` counts its owners and restores the host's inline styles when the last one releases, and `OverlayPlacement` is the shared placement type.
- `notification`: a top-right notification stack for task status and result feedback; see [Notification](#notification).

### `auth/`

What the six forms in `components/auth/` share: the `<form>` frame with title, description, error alert, submit button and agreement consent (`form-frame.tsx`), fields with an optional password toggle (`fields.tsx`), agreement links and the confirmation dialog (`agreements.tsx`), `useForm` for values and validation, `useAuthLabels` for localized labels, and `auth.css`.

### `dashboard/`

`DashboardState`, the loading, error (with a retry button) and empty block, and `dashboard.css` for the state block, tab bar and body that WikiDashboard and ExperienceDashboard share.

### Language

- `locale.ts`: the `Locale` type (`'en-US' | 'zh-CN'`), `resolveLocale` and `mergeLabels`. An explicit `labels` entry beats the locale's default, `undefined` inherits the default, and an empty string stays an explicit override. It holds no business messages.
- `locale-context.tsx`: `useLocale` and `LocaleProvider`, the language scope all public entries share. It is separate from the request client's Context, so switching language keeps requests and state.

Translations stay with their owner in `locales/en-US.ts` and `locales/zh-CN.ts`; `auth/` and `primitives/modal/` follow the same layout.

### Notification

- `useNotification` returns an instance-local API and a context holder. Mount the holder inside the caller's locale and theme scope.
- The API provides `loading`, `info`, `success`, `warning`, `error`, `normal`, `remove` and `clear`; reusing an `id` updates the notice in place, and `btn` supplies actions.
- `loading` uses the built-in `LoadingIcon` shared with Button and stays open by default. Other notices default to 3000 ms; `duration: 0` disables auto-dismiss. Hover and keyboard focus pause the timer.
- The close button and auto-dismiss timer call `onDismiss` before removal begins. Removal and clearing call each notice's `onClose` after its exit animation. Task polling, cancellation and terminal-state actions belong to the caller.
- Only the top-right position is supported, with no global API.
- The [internal task demo](../../../../docs/docs/_demos/notification-tasks.tsx) uses the private docs build and manually simulates running, success and failure states.

## Notes

- Button and Input reuse Arco's interaction logic. Their visual rules adapt `@arco-design/theme-ve-o-aml-design@0.0.36` (MIT) instead of Arco's default skin, with colors mapped to `--ov-ui-color-*` in `src/theme.css` so hosts can override them. No global reset, private icon or font asset is imported. The Arco notice is in [`LICENSE.arco-design`](../../LICENSE.arco-design).
- The documentation site consumes built internal controls through `docs/internal-controls.ts` (the `docs-controls` entry in `rslib.config.ts`). These docs-only artifacts are excluded from npm; `#demo-controls` is for the documentation site, and hosts use their own controls.
- Tests access internal primitives through a private build in `scripts/test.mjs`, not through package exports.

Development commands are in the [workspace README](../../../../README.md); component conventions are in the [components guide](../components/AGENTS.md).
