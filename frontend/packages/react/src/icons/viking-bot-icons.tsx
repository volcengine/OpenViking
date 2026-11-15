import type { SVGProps } from 'react';
import Bulb from '../../../icons/svg/viking-bot/bulb.svg?react';
import Book from '../../../icons/svg/viking-bot/book.svg?react';
import Video from '../../../icons/svg/viking-bot/video.svg?react';
import Report from '../../../icons/svg/viking-bot/report.svg?react';
import History from '../../../icons/svg/viking-bot/history.svg?react';
import NewSession from '../../../icons/svg/viking-bot/new-session.svg?react';
import Remove from '../../../icons/svg/viking-bot/remove.svg?react';
import Tool from '../../../icons/svg/viking-bot/tool.svg?react';
import ContextFolder from '../../../icons/svg/viking-bot/context-folder.svg?react';
import ContextClose from '../../../icons/svg/viking-bot/context-close.svg?react';

const icons = {
  bulb: Bulb,
  book: Book,
  video: Video,
  report: Report,
  history: History,
  newSession: NewSession,
  remove: Remove,
  tool: Tool,
  contextFolder: ContextFolder,
  contextClose: ContextClose,
};
export type VikingBotIconName =
  | 'bulb'
  | 'book'
  | 'video'
  | 'report'
  | 'history'
  | 'newSession'
  | 'remove'
  | 'tool'
  | 'contextFolder'
  | 'contextClose';

/** Reference artwork and the local remove glyph. See packages/icons/README.md. */
export function VikingBotIcon({
  name,
  'aria-label': ariaLabel,
  ...props
}: SVGProps<SVGSVGElement> & { name: VikingBotIconName }) {
  const Icon = icons[name];
  return (
    <Icon
      width={name === 'history' ? 12.667 : 16}
      height={name === 'history' ? 12.667 : 16}
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      focusable="false"
      {...props}
    />
  );
}
