# TUI

English / [中文](README_CN.md)

Required `locale: 'zh-CN' | 'en-US'` selects built-in Chinese or English copy. Existing `labels` override individual fields; omitted fields follow locale. Hosts provide business content.

The view and interaction for command records, results and input. The record area, empty state, input, command suggestions and parameter help make up the TUI body. The host provides command descriptors and execution records. The component reports submission intent through `onSubmit` and copying completion through `onCopy`.

`tui.tsx` maps to `@openviking/react/components/tui`. The view is implemented against the contract below: it renders the command records and results, the empty state, and the controlled input with completion and parameter hints. The type declarations remain the source of truth for the field list.

## Responsibilities

- The TUI contains only the command record and result area, the input area, and its suggestions and help. New sessions, the history panel, view and user switching and the outer toolbar are composed by the host from separate components — they are not part of the TUI, and no slot is provided for them.
- The host handles creating sessions, choosing from history and clearing, then updates `records` and `value`; the TUI renders only the records it is given and keeps no history list or dedicated history-preview state.
- Command execution is entirely the host's. The component uses host-provided command metadata to provide the input interaction, and renders execution results according to the content variant the host supplies.
- Result content is expressed as a union covering generic text and six structured results; results that do not fit are shown as generic text.
- The input is controlled. After accepting a command or example the component updates the input through `onValueChange`; it does not submit the command.
- The composer is a full-width, borderless editing area with a top separator, at least 80px for editing, and a 40px shortcut legend below. Focusing the editor leaves the separator unchanged. Text grows up to three lines, then scrolls. The legend shows `/` for all commands, `↑` to recall commands from the current records, and `↵` to send; Shift+Enter inserts a newline. There is no separate Send button.
- Typing `o` previews `v` in gray; Tab accepts the prefix, and Space completes it to `ov `. Press Tab after `ov ` to open all commands, then type to filter and preview the remaining text; Tab accepts the inline hint or selected command. Ordinary typing shows inline hints; `/` or an explicit Tab opens the list. Completion is derived from the host’s `inputText`. Plain Tab always keeps focus in the composer; without a completion, while entering arguments, or while editing in the middle, it leaves the value and page position unchanged. Shift+Tab moves focus out. Ghost text is never submitted.
- Typing `/`, or pressing Tab after `ov `, opens the list with no selected command. Filtering or reopening clears selection. Down selects the first item; Up selects the last. Hovering or navigating to a command reveals its examples and parameter table. With no keyword and no selected candidate, Tab leaves the input unchanged. The panels stack when horizontal space is limited. Escape and outside clicks dismiss them; browsers without native Popover use an inline panel.
- The editor and command records share a muted CLI prefix, an emphasized command name in the text color, and resource URIs in the link color. Links are underlined on hover. Records use the same `inputText` prefix and preserve their arguments. Timestamps appear on a separate line, aligned right. Copying and history recall use the same prefix; unknown commands retain their original text.
- The host may supply `createdAtMs` (Unix milliseconds): the component shows “Just now”, whole minutes below one hour, whole hours below 24 hours, then a local `YYYY-MM-DD HH:mm` date. Invalid or absent timestamps fall back to the optional `createdAt` display string, which is never parsed. SSR/hydration use that fallback until the client takes over. Times recalculate on rendering without a periodic timer; `labels.justNow`, `labels.formatMinutesAgo` and `labels.formatHoursAgo` customize the localized copy.
- Accepted commands use the host-provided `inputText` prefix (default: `name`), rendered with this hierarchy in the editor. Positional parameter hints appear directly after it, or required flags when no positional parameters exist. Hints disappear while entering arguments and never enter the controlled value. Detailed help appears only while the command list is open.
- `locale` selects built-in English (`en-US`) or Chinese (`zh-CN`) interface copy. `labels` overrides individual strings; hosts localize command and result content. The docs demo follows the site language.
- `onSubmit` reports explicit submission intent. The host owns requests, permissions and execution, and updates pending, successful or failed records through `records`. A pending record does not block later submissions.
- `onOpenUri` receives the raw URI when a link in the input, command bubble or results is activated. The host owns navigation; plain-text links use their native `href` when no handler is supplied.
- Copying is performed by the component and reported through `onCopy` on success, with the copied text as the argument; the callback does not fire on failure. Omitting the callback does not affect copying.
- Command matching, highlighting, keyboard selection, filling and parameter help stay in the component. Hosts supply `commands` and optional `commandGroups`, including localized descriptions, parameters, options and examples. The component does not fetch data or use local storage.
- `quickCommands` lists the command names the built-in empty state shows, resolved from `commands` in array order with unknown names skipped and repeats keeping their first occurrence. The component renders the heading, description and list, and selecting one fills and focuses the input without submitting. `emptyState` replaces that built-in empty state entirely; passing `null` shows nothing, and omitting both shows nothing either.
- Component copy and guidance text are configured uniformly through `labels`. `disabled` independently controls whether the command input and submit are available, and `labels.unavailableMessage` provides the hint while disabled; setting the copy alone does not disable the component.

Command descriptors and groups are host data. New commands using existing descriptor and result types require no component release. The demo catalogue lives in `docs/docs/_demos/tui-commands/`; there is no bundled business catalogue. Without `commands`, text input and submission remain available without suggestions or parameter help.

Result cards share a command heading and an accessible state icon. Matched commands use the host’s `inputText` without arguments and suppress duplicate body titles. Write results shade each line; status rows shade each key/value pair. Resource previews keep their own body layout; JSON and Markdown code blocks share a copyable code view. Execution success does not imply a returned task has finished. Empty results and help copy use `labels.noResult`, `labels.availableCommands` and `labels.helpHint`.

Result rendering maps the seven `TuiRecordContent` variants onto dedicated views: `text` (plain or Markdown, JSON pretty-printed), `search-list`, `resource-preview`, `resource-tree`, `status`, `write-result` and `command-help`. Resource URI links in the input, command bubbles, Markdown and structured result strings share `onOpenUri`. Custom React nodes own their internal links. Clicking an input URI does not change or submit the command; Shift+Tab can focus links for Enter activation. `resource-tree` renders a column-aligned table when items carry `detailRows` and pages at 20 rows otherwise.

## Result content

`TuiRecordContent` has seven variants: `text`, `search-list`, `resource-preview`, `resource-tree`, `status`, `write-result` and `command-help`.

- `text` distinguishes `plain` and `markdown` through `format`, defaulting to `plain`. JSON is formatted and shown as text.
- Failure is expressed through `TuiRecord.status: 'error'`; the error body uses generic text, and no separate error content variant is defined.
- Command help content and all other execution results are supplied by the host using the existing result variants.

Hosts still own the conversion from a CLI response to these variants; the component does not parse command output.

## Optional capabilities

Some fields express host capabilities; omitting one degrades to the corresponding behaviour:

| Field                    | When omitted        | When provided                                                                                    |
| ------------------------ | ------------------- | ------------------------------------------------------------------------------------------------ |
| Empty state              | Nothing shown       | `quickCommands` renders the built-in empty state; `emptyState` replaces it, `null` shows nothing |
| Retrieval level and type | No labels shown     | Shown next to the title and next to the score respectively                                       |
| Score detail             | Score shown as text | When score copy is provided, hover or focus shows the host's detail                              |

While a record is `pending`, the component matches the supplied catalogue and uses its optional `skeleton`; missing or unknown descriptors use the generic shape. `content` may be omitted while waiting.

`disabled` defaults to `false`. When `true` the command input and submit are disabled and `labels.unavailableMessage` is shown; the built-in English hint is used when that copy is not overridden.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/tui/style.css`. Class names follow `ov-tui__<element>` and colours derive from `--ov-ui-*` theme variables with theme-neutral fallbacks, so the console works in light and dark hosts.
