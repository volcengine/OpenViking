import { DocumentIcon } from '../../icons/index.js';
import type { VikingBotLabels, VikingBotToolResultLink } from './model.js';

export function ToolResultLinks({
  results,
  labels,
  onOpenUri,
}: {
  results: readonly VikingBotToolResultLink[];
  labels: VikingBotLabels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-viking-bot__tool-results">
      {results.map((result, index) => (
        <button
          key={`${result.uri}:${index}`}
          type="button"
          className="ov-viking-bot__tool-result"
          disabled={!onOpenUri}
          title={result.uri}
          onClick={() => onOpenUri?.(result.uri)}
        >
          {result.score !== undefined && Number.isFinite(result.score) ? (
            <span className="ov-viking-bot__tool-result-score">
              {labels.similarity} <strong>{result.score.toFixed(2)}</strong>
            </span>
          ) : null}
          <DocumentIcon width={12} height={12} aria-hidden="true" />
          <span className="ov-viking-bot__tool-result-name">
            {result.label ??
              result.uri.replace(/\/$/, '').split('/').pop() ??
              result.uri}
          </span>
          <span className="ov-viking-bot__tool-result-uri">{result.uri}</span>
        </button>
      ))}
    </div>
  );
}
