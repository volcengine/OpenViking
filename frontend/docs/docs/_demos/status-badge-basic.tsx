import { StatusBadge } from '@openviking/react';
import '@openviking/react/style.css';

export default function BasicExample() {
  return (
    <div className="ov-demo-row">
      <StatusBadge status="ready" />
      <StatusBadge status="processing" />
      <StatusBadge status="error" />
    </div>
  );
}
