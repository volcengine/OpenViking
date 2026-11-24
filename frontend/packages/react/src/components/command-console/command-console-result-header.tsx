import type { CommandConsoleRecordStatus } from './types.js';

/** 命令名与执行状态；图标通过名称表达状态。 */
export function CommandConsoleResultHeader({
  command,
  status,
  label,
}: {
  command: string;
  status: CommandConsoleRecordStatus;
  label: string;
}) {
  return (
    <header className="ov-command-console__result-head">
      <span
        className="ov-command-console__result-state"
        data-status={status}
        role="img"
        aria-label={label}
      />
      <span className="ov-command-console__result-command">{command}</span>
    </header>
  );
}
