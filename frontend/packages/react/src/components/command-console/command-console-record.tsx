import {
  getCommandConsoleCommandInputText,
  matchCommandConsoleCommandInput,
} from './command-input.js';
import { CommandConsoleCommandText } from './command-console-command-text.js';
import { CommandConsoleRecordBody } from './command-console-record-body.js';
import type { CommandConsoleRecordProps } from './command-console-record-body.js';
import { CommandConsoleRecordTime } from './command-console-record-time.js';
import { CommandConsoleResultHeader } from './command-console-result-header.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';

const STATUS_TEXT = {
  pending: 'pending',
  success: 'success',
  error: 'error',
} as const;

/** 一条执行记录：命令回显、状态与结果。 */
export function CommandConsoleRecordItem(props: CommandConsoleRecordProps) {
  const { record, labels } = props;
  const statusLabel = labels[STATUS_TEXT[record.status]];
  const input = props.command
    ? matchCommandConsoleCommandInput(record.command, [props.command])
    : undefined;
  const prefix = input ? getCommandConsoleCommandInputText(input.command) : '';

  return (
    <div className="ov-command-console__record" data-status={record.status}>
      <div className="ov-command-console__bubble">
        <code className="ov-command-console__bubble-text">
          {input ? (
            <>
              {input.leadingWhitespace}
              <span className="ov-command-console__bubble-command">
                <CommandConsoleCommandText
                  text={prefix}
                  name={input.command.name}
                />
              </span>
              <CommandConsoleUriText
                text={input.rest}
                mode="literal"
                onOpenUri={props.onOpenUri}
              />
            </>
          ) : (
            <CommandConsoleUriText
              text={record.command}
              mode="literal"
              onOpenUri={props.onOpenUri}
            />
          )}
        </code>
        {record.createdAt || record.createdAtMs !== undefined ? (
          <CommandConsoleRecordTime record={record} labels={labels} />
        ) : null}
      </div>
      <article className="ov-command-console__result">
        <CommandConsoleResultHeader
          command={prefix || record.command}
          status={record.status}
          label={statusLabel}
        />
        <div className="ov-command-console__result-body">
          <CommandConsoleRecordBody {...props} />
        </div>
      </article>
    </div>
  );
}
