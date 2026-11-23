import { forwardRef, type ReactNode } from 'react';

import Chart from '../../../../../icons/svg/empty/chart.svg?react';
import Conversation from '../../../../../icons/svg/empty/conversation.svg?react';
import Crash from '../../../../../icons/svg/empty/crash.svg?react';
import Folder from '../../../../../icons/svg/empty/folder.svg?react';
import People from '../../../../../icons/svg/empty/people.svg?react';
import Search from '../../../../../icons/svg/file-browser/search-empty.svg?react';
import Tray from '../../../../../icons/svg/console/history-empty.svg?react';

const illustrations = {
  chart: Chart,
  conversation: Conversation,
  crash: Crash,
  folder: Folder,
  people: People,
  search: Search,
  tray: Tray,
};

export type EmptyIllustration = keyof typeof illustrations;

/**
 * 空态：插画在上、文案在下，整体居中。插画与文搜索空态同一套 258 × 140 的
 * 虚线网格线稿，颜色取主题变量。`compact` 用在浮层、树和卡片这类小区域。
 *
 * An empty state: artwork above, copy below, centred. The artwork shares the
 * search empty state's 258 × 140 dashed-grid line style and theme colours.
 * `compact` fits popovers, trees and cards. A failure passes `role="alert"` so it is
 * announced at once, and `action` holds its recovery control.
 */
export const Empty = forwardRef<
  HTMLDivElement,
  {
    illustration: EmptyIllustration;
    title: ReactNode;
    description?: ReactNode;
    action?: ReactNode;
    role?: 'status' | 'alert';
    size?: 'default' | 'compact';
    className?: string;
  }
>(function Empty(
  {
    illustration,
    title,
    description,
    action,
    role = 'status',
    size = 'default',
    className,
  },
  ref,
) {
  const Art = illustrations[illustration];
  return (
    <div
      ref={ref}
      className={['ov-empty', `ov-empty--${size}`, className]
        .filter(Boolean)
        .join(' ')}
      role={role}
    >
      <Art className="ov-empty__art" aria-hidden="true" focusable="false" />
      <div className="ov-empty__text">
        <p className="ov-empty__title">{title}</p>
        {description ? (
          <p className="ov-empty__description">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
});
