# Feishu status card: update in place, stop and retry

A reference implementation of one interactive card per task: sent when the
task starts, patched as its state changes, with buttons that act on the task
instead of only feeding a message back to the model.

It is deliberately a few hundred lines over the platform's own JSON. Card
libraries exist for building card *bodies*, and none of them cover the two
things that decide whether this works: the in-place patch, and deciding who may
press a button. Both are small enough to own.

```
card.mjs      task snapshot -> card JSON (pure)
actions.mjs   card click -> authorised, once-only action (pure, injected handlers)
client.mjs    tenant token, send, patch (fetch, no SDK)
demo.mjs      the whole lifecycle without a Feishu app
test/         node:test, no network
```

## Run it

```bash
node demo.mjs                      # dry run: cards, clicks, and the calls that would be made
node --test test/*.test.mjs        # 36 tests, no credentials needed
```

Against a real app (a chat the app is already in):

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx node demo.mjs --send --chat oc_xxx
```

That sends one card and patches it to `done`. Buttons do nothing until a
callback handler is wired, which is the next section.

## Wiring it into a bot

Three points, in this order:

1. **On task start.** Render `taskCard(task)` and send it. Keep the returned
   `message_id` next to the task; that id is the handle for every later update.

   ```js
   const sent = await client.sendCard({ chatId, card, replyTo: originMessageId });
   store.setCardId(task.id, sent.data.message_id);
   ```

2. **On every state change.** Re-render and patch. State transitions are rare
   (queued, running, waiting, done, failed, stopped), so patching on transitions
   rather than on every tool step stays far below the platform's rate limits;
   a progress list that changes per step should be throttled to a few seconds.

   ```js
   await client.patchCard({ messageId, card: taskCard(task) });
   ```

3. **On `card.action.trigger`.** The callback carries the button `value`, the
   operator's `open_id` (trusted) and an event id (for replay). Hand those to
   `TaskCardActions`; patch the card from the result.

   In this daemon a card callback is only accepted for a card it registered
   when it delivered it (`registerCard` in `card-messages.mjs`); an unknown
   message id is refused before reaching this code. Wiring a status card in
   therefore means registering it at delivery and handling its clicks off the
   ingress queue, the way an approval card is (`event.question?.kind`), instead
   of letting them reach the model as `[card action]` input.

   ```js
   const result = await actions.handle({
     value: event.action.value,          // { superagent_task, task_id }
     operatorId: event.operator.open_id, // from the callback, never from the value
     ownerId,
     eventId: event.event_id,
   });
   if (result.outcome === 'stopped') await client.patchCard({ messageId, card: taskCard(task) });
   ```

`getTask` returns the record the scheduler has. The card and the permission
check read both the scheduler's snake_case columns (`requester_id`,
`created_at_ms`, `summary`) and camelCase fields, so `manager.get` can be
passed as is.

`getTask`, `stop` and `retry` are injected:

```js
const actions = new TaskCardActions({
  getTask: (id) => manager.get(id),        // the task record
  stop: (id, reason) => manager.stop(id, reason),
  retry: (id) => manager.create({ ... })   // a new task following the ended one
});
```

## What the card guarantees

- **Only text that cannot become markup.** A task title, a name or a progress
  line is someone's words, not formatting, so every text element is
  `plain_text`. As `lark_md`, a title containing `**` or a tick would restyle
  the card, and escaping it with backslashes assumes an escape syntax the
  renderer does not document (the result being visible backslashes).
- **A button says what to do, never who may do it.** Authorisation compares the
  callback's operator with the task's requester (or the instance owner), so a
  bystander in a group cannot cancel someone else's work. The value on the
  button is treated as external input and re-validated.
- **One click, one effect.** A redelivered callback is recognised by its event
  id; a second click on the same card is ignored for a settling window; two
  callbacks that arrive together find the first still in flight and only one
  action runs. A card whose retry was carried out loses its buttons. `stop` on an
  ended task and `retry` on a task that is still running are refused rather
  than guessed at — and refusing does not spend the card's one click.
- **A dropped update is retried where that cannot duplicate anything.** A patch
  is idempotent, so a 429 or 5xx is retried twice; a send is not, so it is
  retried only when the platform refused it (429 / code 99991400), never on a
  5xx that may have delivered the card already.
- **The card cannot grow past the limit by itself.** Feishu accepts 30 KiB per
  card. Every field is capped where it is read: six progress lines within
  1,200 characters (newest kept), a 120-character title, 240-character note and
  footer. A rendered card stays around 2 KiB, and `client.mjs` refuses anything
  over 30 KiB rather than sending a card the platform will reject.
- **`update_multi: true`.** Without it the patch is accepted and the card keeps
  its first rendering — the failure mode looks like "the status never changes".

## Limits worth knowing

- This patches the whole card. Feishu also offers card entities (CardKit) with
  per-element content updates for typewriter-style streaming, which need a
  second id and a `sequence` counter. Not needed for status cards: nothing
  changes between states.
- Retry starts a new task that follows the ended one. It does not rewind the
  old task, so its history and its result stay as they were.
- The card is not cleaned up when the daemon restarts. The `message_id` has to
  be stored with the task if updates should continue after a restart; the demo
  keeps it in memory only.
- Feishu renders the buttons client-side from the card JSON that was sent.
  Editing a card's buttons after the fact does not revoke a click that was
  already delivered — the idempotency in `actions.mjs` is what makes that safe.
