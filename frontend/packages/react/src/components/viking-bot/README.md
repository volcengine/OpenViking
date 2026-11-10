# VikingBot

English / [中文](README_CN.md)

Optional `locale: 'zh-CN' | 'en-US'` overrides the nearest module or Provider; without either, copy defaults to `en-US`. Existing `labels` override individual fields. Hosts provide business content.

The conversation view: message list, composer and the pending-interaction area. It renders with **Ark Design** (`@ark-design/ui`) — the same library the commercial console uses — and takes an already normalized message model so neither host's protocol leaks in.

> **Status: placeholder.** The API contract is settled and a working view renders against it, but the view has not been through design review and will be rewritten. Treat the props and the interaction model as the deliverable; treat the visuals as provisional.

`viking-bot.tsx` maps to `@openviking/react/components/viking-bot`; the pending-interaction area maps to `@openviking/react/components/viking-bot/interaction`.

## Why one model serves two hosts

The open-source host streams `parts` arrays and renders Markdown with its own stack. The commercial host streams managed-agent events and renders an activity timeline with Ark Design. They differ in message shape, streaming semantics and whether a human-in-the-loop step exists at all.

The component therefore owns **only what both agree on**:

- A message is `{ id, role, parts }`, where `role` covers `user`, `assistant`, `system` and `tool`.
- Every part carries its own discriminator, and `groupId` groups parts that collapse together as one activity block.
- Streaming is `{ parts, insertIndex, iteration }`; a host that appends at the end passes `insertIndex` as the message count.
- A host that renders differently replaces `messageList`, `composer` or `interaction` instead of fighting the built-in views.

Host-specific protocol adaptation stays in each host repository: the component never imports a backend event type.

## What the component owns

- Rendering the message list with Ark's `ChatMessageList`, `Bubble`, `ActivityTimeline`, `MDRenderer` and `ErrorCard`.
- Rendering the composer with Ark's `Sender`.
- Rendering the four built-in interaction forms with Ark's `AskHuman`: `choice`, `text`, `schedule` and `repair`.
- Copying a message body and reporting the intent; the host performs the copy.
- Grouping consecutive assistant messages of one turn and grouping parts by `groupId`.

## What the host owns

- Fetching, streaming connections, session creation and persistence.
- Normalizing its own events into the package's message model.
- Uploading attachments; the component only reports the files the user picked or dropped.
- Deciding when a turn ends and what to do about it.
- The `custom` interaction, when a question needs a tree picker, an uploader or any other host capability.

## Replacing a region

```tsx
<VikingBot
  locale="en-US"
  messages={messages}
  streaming={streaming}
  draft={draft}
  onDraftChange={setDraft}
  onSend={send}
  onCancel={abort}
  messageList={<MyActivityTimeline messages={messages} />}
  composer={<MySender value={draft} onChange={setDraft} onSend={send} />}
/>
```

`children` replaces the whole body. `interaction` takes precedence over `composer`, which is what both hosts do while a question is pending; `interactionSlot` replaces that area wholesale when a host renders its own.

Omitting `draft` renders no composer at all, which is what a read-only transcript needs.

## Interaction

A pending question is host state, not a backend pause: the host derives it from its own stream and clears it when the answer has been sent. The component renders the form and reports the answer; the host turns it into whatever its backend expects.

`choice`, `text`, `schedule` and `repair` render built in. `custom` receives `{ disabled, submitting, submit, cancel }` and renders whatever the host needs, so a tree picker or a skill uploader never has to enter the public package.

## Ark is lazily loaded

Ark pulls in browser-only dependencies that cannot be resolved in a plain Node process, so every entry that renders with it sits behind `lazy()` — the same boundary `FilePreview` uses for its body. The package's top-level entries import without `window`, and Ark is compiled into its own chunks. `@ark-design/ui` stays an optional peer.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/viking-bot/style.css`. It imports the theme baseline and the Ark component stylesheets the view uses.
