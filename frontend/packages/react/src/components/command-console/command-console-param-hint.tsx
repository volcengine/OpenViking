import type {
  CommandConsoleCommandDefinition,
  CommandConsoleCommandOption,
} from './command-catalog.js';
import { getCommandConsoleCommandInputText } from './command-input.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import { getCommandConsoleSafeHref } from './command-console-text.js';

export function CommandConsoleParamHint({
  command,
  labels,
  onPick,
}: {
  command: CommandConsoleCommandDefinition;
  labels: ResolvedCommandConsoleLabels;
  onPick: (value: string) => void;
}) {
  const quickStart = command.quickStart;
  const guideHref = getCommandConsoleSafeHref(quickStart?.documentation?.href);
  const examples = command.examples?.map((example) =>
    typeof example === 'string' ? { value: example, label: example } : example,
  );
  const details = command.parameterDetails;
  const params = [
    ...(command.params ?? []).map((param) => ({
      token: param.label,
      description: param.description,
      required: param.required ?? false,
      defaultValue: param.defaultValue,
    })),
    ...(details?.options ?? command.options ?? []),
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
      {quickStart?.steps.length ? (
        <div className="ov-command-console__hint-group">
          <div className="ov-command-console__hint-label">
            {labels.examples}
          </div>
          {guideHref && quickStart.documentation ? (
            <a
              className="ov-command-console__hint-guide-link"
              href={guideHref}
              target="_blank"
              rel="noreferrer"
            >
              {quickStart.documentation.label}
            </a>
          ) : null}
          <ol className="ov-command-console__hint-steps">
            {quickStart.steps.map(({ description, command: example }) => (
              <li key={description} className="ov-command-console__hint-desc">
                {description}
                {example ? (
                  <button
                    type="button"
                    className="ov-command-console__hint-example"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => onPick(example)}
                  >
                    {example}
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : examples?.length ? (
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
        <code className="ov-command-console__hint-usage">
          {details?.usage ?? command.usage}
        </code>
        {params.length ? (
          <>
            <div className="ov-command-console__hint-desc">
              {labels.quoteArguments}
            </div>
            <ParameterTable params={params} labels={labels} />
          </>
        ) : (
          <div className="ov-command-console__hint-empty">
            {labels.noParameters}
          </div>
        )}
      </div>
      {details?.extra && (
        <div className="ov-command-console__hint-group">
          <div className="ov-command-console__hint-desc">
            {details.extra.description}
          </div>
          <ParameterTable params={details.extra.options} labels={labels} />
        </div>
      )}
    </div>
  );
}

function ParameterTable({
  params,
  labels,
}: {
  params: readonly CommandConsoleCommandOption[];
  labels: ResolvedCommandConsoleLabels;
}) {
  return (
    <div className="ov-command-console__hint-table-frame">
      <table className="ov-command-console__hint-table">
        <thead>
          <tr>
            <th scope="col">{labels.parameter}</th>
            <th scope="col">{labels.required}</th>
            <th scope="col">{labels.description}</th>
            <th scope="col">{labels.defaultValue}</th>
          </tr>
        </thead>
        <tbody>
          {params.map((param) => (
            <tr key={param.token}>
              <td>
                <code>{param.token}</code>
              </td>
              <td>{param.required ? labels.yes : labels.no}</td>
              <td>{param.description}</td>
              <td>{param.defaultValue ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
