import type { TuiRecordStatus } from './types.js';

/** 命令名与执行状态；图标通过名称表达状态。 */
export function TuiResultHeader({
  command,
  status,
  label,
}: {
  command: string;
  status: TuiRecordStatus;
  label: string;
}) {
  return (
    <header className="ov-tui__result-head">
      <span
        className="ov-tui__result-state"
        data-status={status}
        role="img"
        aria-label={label}
      />
      <span className="ov-tui__result-command">{command}</span>
    </header>
  );
}
