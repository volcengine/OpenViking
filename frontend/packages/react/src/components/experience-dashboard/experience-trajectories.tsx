import dayjs from 'dayjs';

import { ArrowUpRightIcon, FileLinkIcon } from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { ExperienceCopyAction } from './experience-copy-action.js';
import { useLoadMoreOnScroll } from './use-load-more-on-scroll.js';
import type {
  ExperienceDashboardLabels,
  ExperienceOutcome,
  ExperienceTrajectory,
} from './types.js';

/** 只给有语义的三种结果上色，其余保持中性。 Only these three outcomes are coloured. */
const OUTCOME_CLASS: Partial<Record<ExperienceOutcome, string>> = {
  success: 'ov-experience-dashboard__outcome-tag--success',
  failure: 'ov-experience-dashboard__outcome-tag--failure',
  partial: 'ov-experience-dashboard__outcome-tag--partial',
};

/**
 * 百分号解码，解不开就原样返回。
 *
 * URI 契约并不保证文件名是合法的百分号编码——`discount-50%.md` 这种裸 `%` 会让
 * `decodeURIComponent` 抛 `URIError`。它跑在渲染里，一旦抛出整个看板就白屏，
 * 所以这里兜住异常，宁可显示未解码的名字。
 *
 * Percent-decoding that falls back to the input.
 *
 * The URI contract never promises a valid percent encoding — a bare `%` such as
 * `discount-50%.md` makes `decodeURIComponent` throw `URIError`. That runs during
 * render, so an uncaught throw would blank the whole board; catch it and show the
 * name undecoded instead.
 */
function decodeFileName(file: string) {
  try {
    return decodeURIComponent(file);
  } catch {
    return file;
  }
}

/**
 * 轨迹标题：服务端有时返回空 `name`，此时回退到 URI 的文件名，否则整行只剩图标。
 *
 * The trajectory title: the server sometimes returns an empty `name`; fall back to
 * the file name in the URI, or the row would be left with only an icon.
 */
function titleOf(item: ExperienceTrajectory) {
  if (item.name) return item.name;
  const file = item.uri.split('/').filter(Boolean).at(-1);
  return file ? decodeFileName(file) : item.uri;
}

/**
 * 轨迹时间：用 dayjs 解析，交给 `Intl` 按界面语言格式化。
 *
 * 解析走 dayjs 是仓库约定（日期算术与解析统一用它）；`Intl` 只负责给人看的格式。
 * 解析不出来时原样返回，避免把无效时间显示成 1970。
 *
 * The trajectory time: parsed with dayjs, formatted by `Intl` for the interface
 * language.
 *
 * Parsing through dayjs is the repository rule (all date arithmetic and parsing uses
 * it); `Intl` only renders the human-facing format. An unparseable value is returned
 * as-is rather than shown as 1970.
 */
function timeOf(value: string, locale: string) {
  const time = dayjs(value);
  if (!time.isValid()) return value;
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(time.toDate());
}

/**
 * 轨迹时间线：左侧一条竖线加圆点，右侧依次是时间、标题与结果、URI 与两个操作。
 *
 * 结构与火山版一致——URI 前的文件图标、复制与「在上下文管理中打开」两个图标
 * 按钮，都是轨迹行固定的一部分。
 *
 * The trajectory timeline: a rail with a dot on the left, then the time, the title
 * with its outcome, and the URI with two actions on the right.
 *
 * The structure follows the console: the file icon before the URI and the copy and
 * "open in context management" icon buttons are part of every row.
 */
export function ExperienceTrajectories({
  items,
  labels: t,
  locale = 'en-US',
  hasMore = false,
  loadingMore = false,
  scrollLoadMore = false,
  onLoadMore,
  onOpenUri,
}: {
  items: readonly ExperienceTrajectory[];
  labels: ExperienceDashboardLabels;
  locale?: 'en-US' | 'zh-CN';
  hasMore?: boolean;
  loadingMore?: boolean;
  /**
   * 滚到底部时自动追加，与可点击的按钮并存。来源追溯只有一页，不需要它。
   *
   * Appends on reaching the bottom, alongside the clickable button. The source trace
   * holds a single page and does not need it.
   */
  scrollLoadMore?: boolean;
  onLoadMore?: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const sentinelRef = useLoadMoreOnScroll({
    enabled: scrollLoadMore && hasMore,
    loading: loadingMore,
    onLoadMore,
  });
  return (
    <>
      <ol className="ov-experience-dashboard__timeline">
        {items.map((item) => (
          <li key={item.uri} className="ov-experience-dashboard__timeline-item">
            <span
              className="ov-experience-dashboard__timeline-rail"
              aria-hidden="true"
            >
              <i />
            </span>
            <div className="ov-experience-dashboard__timeline-content">
              {item.created_at ? (
                <time dateTime={item.created_at}>
                  {timeOf(item.created_at, locale)}
                </time>
              ) : null}
              <div className="ov-experience-dashboard__trajectory-title">
                {/* 标题只是文字：打开动作由右侧的图标按钮负责，与控制台一致。
                    两处都调 `onOpenUri` 会让同一行出现两个指向同一目标的入口。
                 *
                 * The title is plain text: the icon button on the right owns the open
                 * action, as in the console. Two triggers for one destination would make
                 * the same row offer the same trip twice. */}
                <strong className="ov-experience-dashboard__trajectory-name">
                  {titleOf(item)}
                </strong>
                {item.outcome ? (
                  <span
                    className={[
                      'ov-experience-dashboard__outcome-tag',
                      OUTCOME_CLASS[item.outcome] ?? '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    <i aria-hidden="true" />
                    {t[item.outcome]}
                  </span>
                ) : null}
              </div>
              <div className="ov-experience-dashboard__trajectory-path">
                <FileLinkIcon aria-hidden="true" width={12} height={12} />
                <span title={item.uri}>{item.uri}</span>
                <span className="ov-experience-dashboard__path-actions">
                  <ExperienceCopyAction value={item.uri} labels={t} />
                  <Tooltip content={t.open} placement="top">
                    <button
                      type="button"
                      className="ov-experience-dashboard__icon-button"
                      aria-label={t.open}
                      disabled={!onOpenUri}
                      onClick={() => onOpenUri?.(item.uri)}
                    >
                      <ArrowUpRightIcon width={12} height={12} />
                    </button>
                  </Tooltip>
                </span>
              </div>
              {item.description ? (
                <p className="ov-experience-dashboard__trajectory-description">
                  {item.description}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {hasMore || loadingMore ? (
        <div ref={sentinelRef} className="ov-experience-dashboard__load-more">
          {loadingMore ? (
            t.loadingMore
          ) : (
            <button
              type="button"
              className="ov-experience-dashboard__load-more-action"
              disabled={!onLoadMore}
              onClick={onLoadMore}
            >
              {t.loadMore}
            </button>
          )}
        </div>
      ) : null}
    </>
  );
}
