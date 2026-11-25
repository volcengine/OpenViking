import Conversation from '../../../icons/svg/console/conversation.svg?react';
import Terminal from '../../../icons/svg/console/terminal.svg?react';
import History from '../../../icons/svg/viking-bot/history.svg?react';
import NewSession from '../../../icons/svg/viking-bot/new-session.svg?react';

const icons = {
  conversation: Conversation,
  command: Terminal,
  history: History,
  newSession: NewSession,
};

/** Private Console controls. Sources and licenses are in packages/icons/README.md. */
export function ConsoleIcon({ name }: { name: keyof typeof icons }) {
  const Icon = icons[name];
  const size = name === 'history' ? 12.667 : 16;
  return (
    <Icon width={size} height={size} aria-hidden="true" focusable="false" />
  );
}
