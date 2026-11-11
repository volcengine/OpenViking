import type { ExperienceTrajectory } from './types.js';

export function ExperienceTrajectories({
  items,
  onOpenUri,
  source = false,
}: {
  items: readonly ExperienceTrajectory[];
  onOpenUri?: (uri: string) => void;
  source?: boolean;
}) {
  return (
    <ul
      className={`ov-experience-dashboard__timeline${source ? ' ov-experience-dashboard__timeline--source' : ''}`}
    >
      {items.map((item) => (
        <li key={item.uri}>
          {item.created_at ? (
            <time dateTime={item.created_at}>{item.created_at}</time>
          ) : null}
          <button
            type="button"
            disabled={!onOpenUri}
            onClick={() => onOpenUri?.(item.uri)}
          >
            {item.name}
          </button>
          <code>{item.uri}</code>
          {item.description ? <p>{item.description}</p> : null}
        </li>
      ))}
    </ul>
  );
}
