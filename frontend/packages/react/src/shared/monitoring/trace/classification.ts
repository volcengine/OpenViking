import type { ErrorKind } from '../../../client/monitoring/fault.js';
import { classify } from '../fault.js';
import type { FailureName, FaultRecord } from './span-fault.js';

export function faultClassification(
  name: FailureName,
  error: unknown,
  kind: ErrorKind,
  cause?: FaultRecord,
) {
  let fault = cause && kind !== 'render' ? cause.fault : classify(error, kind);
  if (kind === 'render') return classify(undefined, 'render');
  if (name === 'console_chat_history_save_fail')
    return classify(
      undefined,
      kind === 'host-service' ? 'host-service' : 'storage',
    );
  if (name === 'task_poll_protocol_fail')
    return { errorKind: 'protocol', code: 'TASK_RESPONSE_INVALID' } as const;
  if (
    fault.errorKind === 'protocol' &&
    /^(file_browser|console_session)_/.test(name)
  )
    return { errorKind: 'protocol', code: 'DATA_RESPONSE_INVALID' } as const;
  const resource =
    /^(file_preview_(viewer|editor)|viking_bot_(composer|message_list|interaction))_load_fail$/.test(
      name,
    );
  if (
    resource &&
    !['resource-load', 'configuration', 'unknown'].includes(fault.errorKind)
  )
    fault = classify(undefined, 'unknown');
  if (
    name === 'task_poll_request_fail' &&
    ![
      'network',
      'http',
      'business',
      'configuration',
      'response-read',
      'host-service',
      'unknown',
    ].includes(fault.errorKind)
  )
    fault = classify(undefined, 'host-service');
  return fault;
}
