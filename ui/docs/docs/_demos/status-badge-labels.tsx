import { StatusBadge } from '@openviking/ui';
import '@openviking/ui/style.css';

export default function CustomLabelsExample() {
  return (
    <div className="ov-demo-row">
      <StatusBadge status="ready">Connected</StatusBadge>
      <StatusBadge status="processing">Indexing resources</StatusBadge>
      <StatusBadge status="error">Connection failed</StatusBadge>
    </div>
  );
}
