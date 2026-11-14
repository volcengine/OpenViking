import type { StageName } from '../../../client/monitoring/targets.js';
import type {
  TraceSnapshot,
  Trigger,
} from '../../../client/monitoring/trace.js';
import type { MonitoringBinding } from '../binding.js';
import { MonitoringSpan } from './span.js';

export function startSpan(
  binding: MonitoringBinding | undefined,
  stage: StageName,
  trigger: Trigger = 'programmatic',
  retryOf?: TraceSnapshot['links'][number],
) {
  if (!binding?.active() || !binding.controller.options) return;
  if (
    !retryOf &&
    binding.parent?.valid() &&
    binding.parent.controller === binding.controller
  )
    return binding.parent.child(stage, binding);
  const span = new MonitoringSpan(binding, stage, retryOf ? 'retry' : trigger);
  if (retryOf) span.history.addLink(retryOf);
  const gap = binding.contextGap?.();
  if (gap) span.gap(gap);
  if (binding.parent?.available() && !binding.parent.valid() && !retryOf)
    span.link(binding.parent, 'render-input');
  else if (binding.parent && !retryOf)
    span.gap(
      binding.parent.controller === binding.controller
        ? 'started-before-enabled'
        : 'disabled-scope',
    );
  return span;
}
