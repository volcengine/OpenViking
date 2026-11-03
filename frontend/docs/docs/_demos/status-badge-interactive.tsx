import { useState } from 'react';
import {
  StatusBadge,
  type StatusBadgeStatus,
} from '@openviking/react/components/status-badge';
import '@openviking/react/components/status-badge/style.css';

const states: StatusBadgeStatus[] = ['ready', 'processing', 'error'];

export default function InteractiveExample() {
  const [status, setStatus] = useState<StatusBadgeStatus>('ready');

  return (
    <div>
      <div aria-live="polite" aria-atomic="true">
        <StatusBadge status={status} />
      </div>
      <div
        className="ov-demo-controls"
        role="group"
        aria-label="Resource state"
      >
        {states.map((state) => (
          <button
            key={state}
            type="button"
            aria-pressed={status === state}
            onClick={() => setStatus(state)}
          >
            {state.charAt(0).toUpperCase() + state.slice(1)}
          </button>
        ))}
      </div>
    </div>
  );
}
