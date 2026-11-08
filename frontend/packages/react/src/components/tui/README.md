# TUI

English / [中文](README_CN.md)

The view and interaction for command records, results and input. The record area, empty state, input, command suggestions and parameter help make up the TUI body. Execution results are passed in from above; command sending, resource opening and copy completion are reported back to the host through callbacks, and a failure does not fire the corresponding callback.

`tui.tsx` maps to `@openviking/react/components/tui`. The view is implemented against the contract below: it renders the command records and results, the empty state, and the controlled input with completion and parameter hints. The type declarations remain the source of truth for the field list.

## Responsibilities

- The TUI contains only the command record and result area, the input area, and its suggestions and help. New sessions, the history panel, view and user switching, the outer toolbar and the bottom shortcut hints are composed by the host from separate components — they are not part of the TUI, and no slot is provided for them.
- The host handles creating sessions, choosing from history and clearing, then updates `records` and `value`; the TUI renders only the records it is given and keeps no history list or dedicated history-preview state.
- Command execution is entirely the host's. The component reuses shared command metadata to provide the input interaction, and renders execution results according to the content variant the host supplies.
- Result content is expressed as a union covering generic text and six structured results; results that do not fit are shown as generic text.
- The input is controlled. After accepting a completion or parameter hint the component updates the input through `onValueChange`; it does not submit the command.
- `onSubmit` reports the command text only after the command has been sent successfully, and does not fire on failure. That notification does not perform the send, and does not mean the background command has finished executing; the send path does not depend on its return value. The host updates later results and errors through `records`; a background task still `pending` does not block further submissions.
- `onOpenUri` reports the URI after a resource has been opened successfully, and does not fire on failure; the navigation capability belongs to the integration layer.
- Copying is performed by the component and reported through `onCopy` on success, with the copied text as the argument; the callback does not fire on failure. Omitting the callback does not affect copying.
- Command matching, the candidate list, highlighting and keyboard selection, parameter hints and the help panel are all handled inside the component. Command names, parameters, descriptions and examples must reuse the same CLI command metadata; the host does not maintain or render command help entry by entry, and the component neither fetches data nor reads or writes local storage.
- `emptyState` is the single configuration entry for the complete empty state and can combine a title, description and shortcut entries, with the related interaction handled by the host inside the node. It is shown when there are no records; when omitted, `labels.empty` is used, and passing `null` hides the empty-state content.
- Component copy and guidance text are configured uniformly through `labels`. `disabled` independently controls whether the command input and submit are available, and `labels.unavailableMessage` provides the hint while disabled; setting the copy alone does not disable the component.

Command metadata lives in `command-core.ts`, `command-filesystem.ts` and `command-session.ts`, grouped like the `ov` CLI help. It is bundled data, not fetched: the component stays render-only, so a host that needs server-provided commands converts them to records instead of extending the input. When the CLI adds a command, add it to the matching file to make it appear in completion, parameter hints and the help record.

Result rendering maps the seven `TuiRecordContent` variants onto dedicated views: `text` (plain or Markdown, JSON pretty-printed), `search-list`, `resource-preview`, `resource-tree`, `status`, `write-result` and `command-help`. URIs inside text results become openable entries. `resource-tree` renders a column-aligned table when items carry `detailRows` and pages at 20 rows otherwise.

## Result content

`TuiRecordContent` has seven variants: `text`, `search-list`, `resource-preview`, `resource-tree`, `status`, `write-result` and `command-help`.

- `text` distinguishes `plain` and `markdown` through `format`, defaulting to `plain`. JSON is formatted and shown as text.
- Failure is expressed through `TuiRecord.status: 'error'`; the error body uses generic text, and no separate error content variant is defined.
- Command help is generated from the shared command definitions, and other results are converted uniformly by the shared adapter layer; the host reuses the converted result rather than adapting commands one by one.

Hosts still own the conversion from a CLI response to these variants; the component does not parse command output.

## Optional capabilities

Some fields express host capabilities; omitting one degrades to the corresponding behaviour:

| Field                    | When omitted         | When provided                                                       |
| ------------------------ | -------------------- | ------------------------------------------------------------------- |
| Empty-state node         | Shows `labels.empty` | Shows the full node when there are no records; `null` hides it      |
| Retrieval level and type | No labels shown      | Shown next to the title and next to the score respectively          |
| Score detail             | Score shown as text  | When score copy is provided, hover or focus shows the host's detail |

While a record is `pending`, the component matches `command` against the shared command definitions and picks the corresponding skeleton, falling back to the generic skeleton when nothing matches. `content` may be omitted while waiting; the host does not need to specify a skeleton shape.

`disabled` defaults to `false`. When `true` the command input and submit are disabled and `labels.unavailableMessage` is shown; the built-in English hint is used when that copy is not overridden.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/tui/style.css`. Class names follow `ov-tui__<element>` and colours derive from `--ov-ui-*` theme variables with theme-neutral fallbacks, so the console works in light and dark hosts.
