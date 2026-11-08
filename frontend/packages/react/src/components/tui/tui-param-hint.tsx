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
  return (
    <div className="ov-tui__hint">
      <div className="ov-tui__hint-head">
        <code className="ov-tui__hint-name">/{command.name}</code>
        <span className="ov-tui__hint-desc">{command.description}</span>
      </div>
      <code className="ov-tui__hint-usage">{command.usage}</code>
      {!command.params?.length && !command.options?.length ? (
        <div className="ov-tui__hint-empty">{labels.noParameters}</div>
      ) : null}
      {command.params?.length ? (
        <div className="ov-tui__hint-group">
          <div className="ov-tui__hint-label">{labels.parameters}</div>
          {command.params.map((param) => (
            <div key={param.label} className="ov-tui__hint-row">
              <code>{param.label}</code>
              <span>{param.description}</span>
            </div>
          ))}
        </div>
      ) : null}
      {command.options?.length ? (
        <div className="ov-tui__hint-group">
          <div className="ov-tui__hint-label">{labels.options}</div>
          {command.options.map((option) => (
            <div key={option.token} className="ov-tui__hint-row">
              <code>{option.token}</code>
              <span>{option.description}</span>
            </div>
          ))}
        </div>
      ) : null}
      {command.examples?.length ? (
        <div className="ov-tui__hint-group">
          <div className="ov-tui__hint-label">{labels.examples}</div>
          {command.examples.map((example) => (
            <button
              key={example}
              type="button"
              className="ov-tui__hint-example"
              onClick={() => onPick(example)}
            >
              {example}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
