import type { SVGProps } from 'react';
import Bulb from '../../../icons/svg/viking-bot/bulb.svg?react';
import Book from '../../../icons/svg/viking-bot/book.svg?react';
import Video from '../../../icons/svg/viking-bot/video.svg?react';
import Report from '../../../icons/svg/viking-bot/report.svg?react';
import History from '../../../icons/svg/viking-bot/history.svg?react';
import NewSession from '../../../icons/svg/viking-bot/new-session.svg?react';
import Remove from '../../../icons/svg/viking-bot/remove.svg?react';
import Tool from '../../../icons/svg/viking-bot/tool.svg?react';

const icons = {
  bulb: Bulb,
  book: Book,
  video: Video,
  report: Report,
  history: History,
  newSession: NewSession,
  remove: Remove,
  tool: Tool,
};
export type VikingBotIconName =
  | 'bulb'
  | 'book'
  | 'video'
  | 'report'
  | 'history'
  | 'newSession'
  | 'remove'
  | 'tool';

/** Reference artwork and the local remove glyph. See packages/icons/README.md. */
export function VikingBotIcon({
  name,
  'aria-label': ariaLabel,
  ...props
}: SVGProps<SVGSVGElement> & { name: VikingBotIconName }) {
  const Icon = icons[name];
  return (
    <Icon
      width={16}
      height={16}
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      focusable="false"
      {...props}
    />
  );
}
