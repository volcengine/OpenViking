import { Button } from '../../shared/components/button/button.js';
import { Tooltip } from '../../shared/components/tooltip/tooltip.js';
import type { WikiDashboardAction } from './types.js';

export function WikiActionButton({
  icon,
  tooltip,
  disabled,
  onClick,
}: Omit<WikiDashboardAction, 'key'>) {
  const inactive = disabled || !onClick;
  return (
    <Tooltip content={tooltip}>
      <Button
        size="mini"
        type="text"
        iconOnly
        htmlType="button"
        aria-label={tooltip}
        aria-disabled={inactive || undefined}
        icon={icon}
        onClick={onClick}
      />
    </Tooltip>
  );
}
