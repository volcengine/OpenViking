import { getTuiCommandInputText } from './command-input.js';
import type { TuiCommandDefinition } from './command-catalog.js';
import type { ResolvedTuiLabels } from './tui-labels.js';

export function TuiParamHint({
  command,
  labels,
  onPick,
}: {
  command: TuiCommandDefinition;
  labels: ResolvedTuiLabels;
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
    <div className="ov-tui__hint">
      <div className="ov-tui__hint-head">
        <code className="ov-tui__hint-name">
          {getTuiCommandInputText(command)}
        </code>
        <span className="ov-tui__hint-desc">{command.description}</span>
      </div>
      {examples?.length ? (
        <div className="ov-tui__hint-group">
          <div className="ov-tui__hint-label">{labels.examples}</div>
          {examples.map((example, index) => (
            <button
              key={example.value}
              type="button"
              className="ov-tui__hint-example"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onPick(example.value)}
            >
              <span className="ov-tui__hint-example-index" aria-hidden="true">
                {index + 1}
              </span>
              <span>{example.label ?? example.value}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="ov-tui__hint-group">
        <div className="ov-tui__hint-section-title">{labels.parameters}</div>
        <code className="ov-tui__hint-usage">{command.usage}</code>
        {params.length ? (
          <div className="ov-tui__hint-table-frame">
            <table className="ov-tui__hint-table">
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
          <div className="ov-tui__hint-empty">{labels.noParameters}</div>
        )}
      </div>
    </div>
  );
}
