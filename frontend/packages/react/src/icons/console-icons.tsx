import Conversation from '../../../icons/svg/console/conversation.svg?react';
import Terminal from '../../../icons/svg/console/terminal.svg?react';
import History from '../../../icons/svg/console/history.svg?react';
import NewSession from '../../../icons/svg/console/new-session.svg?react';
import HistoryEmpty from '../../../icons/svg/console/history-empty.svg?react';

const icons = {
  conversation: Conversation,
  command: Terminal,
  history: History,
  newSession: NewSession,
  historyEmpty: HistoryEmpty,
};

/** Private Console controls. Artwork is authored for this workspace. */
export function ConsoleIcon({ name }: { name: keyof typeof icons }) {
  const Icon = icons[name];
  return <Icon aria-hidden="true" focusable="false" />;
}
