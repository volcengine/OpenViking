/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- Labelled overflow region must support keyboard scrolling. */
import type { ExperienceDashboardLabels, ExperienceItem } from './types.js';

function updatedAt(value: string, locale: 'en-US' | 'zh-CN') {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return value;
  const seconds = (time - Date.now()) / 1000;
  const absolute = Math.abs(seconds);
  if (absolute >= 7 * 86400)
    return new Intl.DateTimeFormat(locale).format(time);
  const [unit, size] =
    absolute < 3600
      ? (['minute', 60] as const)
      : absolute < 86400
        ? (['hour', 3600] as const)
        : (['day', 86400] as const);
  return new Intl.RelativeTimeFormat(locale, { numeric: 'always' }).format(
    Math.round(seconds / size),
    unit,
  );
}

export function ExperienceList({
  items,
  total,
  startIndex = 0,
  locale = 'en-US',
  labels: t,
  onOpenUri,
}: {
  items: readonly ExperienceItem[];
  total?: number;
  startIndex?: number;
  locale?: 'en-US' | 'zh-CN';
  labels: ExperienceDashboardLabels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      <h2 className="ov-experience-dashboard__list-title">
        {t.allExperiences}
        {total === undefined ? '' : ` · ${total}`}
      </h2>
      <div
        className="ov-experience-dashboard__table-scroll"
        tabIndex={0}
        role="region"
        aria-label={t.title}
      >
        <table className="ov-experience-dashboard__table">
          <caption>{t.title}</caption>
          <thead>
            <tr>
              <th scope="col">{t.index}</th>
              <th scope="col">{t.name}</th>
              <th scope="col">{t.injection}</th>
              <th scope="col">{t.listRecall}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) => (
              <tr key={item.uri}>
                <td># {startIndex + index + 1}</td>
                <th scope="row">
                  <button
                    type="button"
                    disabled={!onOpenUri}
                    onClick={() => onOpenUri?.(item.uri)}
                  >
                    {item.name}
                  </button>
                  <span>
                    <code title={item.uri}>{item.uri}</code>
                    {item.modTime ? (
                      <time dateTime={item.modTime} title={item.modTime}>
                        · {t.updated} {updatedAt(item.modTime, locale)}
                      </time>
                    ) : null}
                  </span>
                </th>
                <td>{item.injectionCount ?? '—'}</td>
                <td>{item.recallCount ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
