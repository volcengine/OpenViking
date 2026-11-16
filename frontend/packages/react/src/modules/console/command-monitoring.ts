import type { OvClient } from '../../client/types.js';
import type { ConsoleRecord } from './command-messages.js';
import { bindingOf, bindClient } from '../../shared/monitoring/client.js';
import { scopeBinding } from '../../shared/monitoring/binding.js';
import { readData, tagData } from '../../shared/monitoring/data.js';
import { startSpan } from '../../shared/monitoring/trace/span.js';

export function observeCommand(client: OvClient, signal: AbortSignal) {
  const binding = bindingOf(client);
  const span =
    binding &&
    startSpan(
      scopeBinding(binding, 'commands', null),
      'console-command-dispatch',
      'submit',
    );
  if (!span) return;
  const abort = () => span.finish('cancelled', 'user-cancel');
  signal.addEventListener('abort', abort, { once: true });
  return {
    span,
    client: bindClient(client, { ...span.binding, parent: span }),
    finish(content?: object) {
      signal.removeEventListener('abort', abort);
      span.finish(content ? 'ready' : 'failed');
      tagData(binding, content, [span]);
    },
  };
}
export function tagCommandRecords(
  client: OvClient,
  records?: readonly ConsoleRecord[],
) {
  if (!records) return;
  const binding = bindingOf(client);
  tagData(
    binding,
    records,
    readData(
      binding,
      records.map((record) => record.content),
    ),
  );
}
