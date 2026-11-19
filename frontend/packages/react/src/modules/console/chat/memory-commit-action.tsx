import CommitIcon from '../../../../../icons/svg/viking-bot/session-commit.svg?react';
import CheckIcon from '../../../../../icons/svg/viking-bot/check.svg?react';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';

type Action = NonNullable<VikingBotMessage['action']>;
export const memoryCommitActionKey = 'commit-session';

export function memoryCommitAction(
  label: string,
  status: Action['status'],
): Action {
  return {
    key: memoryCommitActionKey,
    label,
    status,
    appearance: 'pill',
    icon: status === 'success' ? <CheckIcon /> : <CommitIcon />,
    loading: status === 'pending' || status === 'submitted',
  };
}
