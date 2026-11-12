import { getCommandConsoleCommandInputText } from './command-input.js';
import type { CommandConsoleCommandDefinition } from './command-catalog.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';

export function CommandConsoleParamHint({
  command,
  labels,
  onPick,
}: {
  command: CommandConsoleCommandDefinition;
  labels: ResolvedCommandConsoleLabels;
  onPick: (value: string) => void;
}) {
  const examples = command.examples?.map((example) =>
    typeof example === 'string' ? { value: example, label: example } : example,
  );
  const params = [
    ...(command.params ?? []).map((param) => ({
      name: param.label,
      description: param.description,
      required: param.required ?? false,
      defaultValue: param.defaultValue,
    })),
    ...(command.options ?? []).map((option) => ({
      name: option.token,
      description: option.description,
      required: option.required ?? false,
      defaultValue: option.defaultValue,
    })),
  ];
  return (
    <div className="ov-command-console__hint">
      <div className="ov-command-console__hint-head">
        <code className="ov-command-console__hint-name">
          {getCommandConsoleCommandInputText(command)}
        </code>
        <span className="ov-command-console__hint-desc">
          {command.description}
        </span>
      </div>
      {examples?.length ? (
        <div className="ov-command-console__hint-group">
          <div className="ov-command-console__hint-label">
            {labels.examples}
          </div>
          {examples.map((example, index) => (
            <button
              key={example.value}
              type="button"
              className="ov-command-console__hint-example"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onPick(example.value)}
            >
              <span
                className="ov-command-console__hint-example-index"
                aria-hidden="true"
              >
                {index + 1}
              </span>
              <span>{example.label ?? example.value}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="ov-command-console__hint-group">
        <div className="ov-command-console__hint-section-title">
          {labels.parameters}
        </div>
        <code className="ov-command-console__hint-usage">{command.usage}</code>
        {params.length ? (
          <div className="ov-command-console__hint-table-frame">
            <table className="ov-command-console__hint-table">
              <thead>
                <tr>
                  <th>{labels.parameter}</th>
                  <th>{labels.required}</th>
                  <th>{labels.description}</th>
                  <th>{labels.defaultValue}</th>
                </tr>
              </thead>
              <tbody>
                {params.map((param) => (
                  <tr key={param.name}>
                    <td>
                      <code>{param.name}</code>
                    </td>
                    <td>{param.required ? labels.yes : labels.no}</td>
                    <td>{param.description}</td>
                    <td>{param.defaultValue ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ov-command-console__hint-empty">
            {labels.noParameters}
          </div>
        )}
      </div>
    </div>
  );
}
