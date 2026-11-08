# VikingBot

English / [中文](README_CN.md)

The conversation view: message list, composer and the pending-interaction area. One component serves both OpenViking hosts, which speak different protocols, by taking an already normalized message model and letting each host replace the parts it renders differently.

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

- Rendering the message list, including reasoning, tool calls and results, context references, images, attachments and iteration dividers.
- Copying a message body and reporting the intent; the host performs the copy.
- Rendering the composer, its attachment list and the send/stop switch.
- Rendering the four built-in interaction forms: `choice`, `text`, `schedule` and `repair`.
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

`children` replaces the whole body. `interaction` takes precedence over `composer`, which is what both hosts do while a question is pending.

## Interaction

A pending question is host state, not a backend pause: the host derives it from its own stream and clears it when the answer has been sent. The component renders the form and reports the answer; the host turns it into whatever its backend expects.

`choice`, `text`, `schedule` and `repair` render built in. `custom` receives `{ disabled, submitting, submit, cancel }` and renders whatever the host needs, so a tree picker or a skill uploader never has to enter the public package.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/viking-bot/style.css`, which includes the message list, composer and interaction styles.
