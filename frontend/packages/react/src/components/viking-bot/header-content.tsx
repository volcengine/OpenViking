import type { VikingBotProps } from './types.js';
import { VikingBotSessionActions } from './session-actions.js';

type Props = Pick<
  VikingBotProps,
  | 'header'
  | 'sessions'
  | 'headerActions'
  | 'onOpenUri'
  | 'onCopy'
  | 'labels'
  | 'locale'
>;
/** Preserve an explicit host header, including null, before falling back to session controls. */
export function renderHeaderContent({
  header,
  sessions,
  headerActions,
  ...props
}: Props) {
  return header !== undefined ? (
    header
  ) : sessions || headerActions ? (
    <VikingBotSessionActions
      {...props}
      sessions={sessions}
      extra={headerActions}
    />
  ) : null;
}
