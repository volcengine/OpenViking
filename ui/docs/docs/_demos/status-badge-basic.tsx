import { StatusBadge } from '@openviking/ui';
import '@openviking/ui/style.css';

export default function BasicExample() {
  return (
    <div className="ov-demo-row">
      <StatusBadge status="ready" />
      <StatusBadge status="processing" />
      <StatusBadge status="error" />
    </div>
  );
}
