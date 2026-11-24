import { useCallback } from 'react';
import type { RefObject } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { ExperienceTrajectoryDetail } from '../../components/experience-dashboard/experience-trajectory-detail.js';
import type { ExperienceTrajectory } from '../../components/experience-dashboard/types.js';
import type { Locale } from '../../shared/locale.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { readFileContent } from './fs-read-content.js';
import { useDashboardQuery } from './use-dashboard-query.js';

type LoadMessages = NonNullable<
  FileBrowserExperienceDataSource['trajectoryMessages']
>;
type LoadOptions = OvRequestOptions & { signal: AbortSignal };

export function ExperienceTrajectoryDetailPane({
  trajectory,
  triggerRef,
  requestOptions,
  loadMessages,
  locale,
  onClose,
  onOpenUri,
}: {
  trajectory: ExperienceTrajectory;
  triggerRef: RefObject<HTMLButtonElement | null>;
  requestOptions?: OvRequestOptions;
  loadMessages: LoadMessages;
  locale?: Locale;
  onClose: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const { uri } = trajectory;
  const readDocument = useCallback(
    async (client: OvClient, options: LoadOptions) =>
      (await readFileContent(client, uri, options)).content,
    [uri],
  );
  const readMessages = useCallback(
    async (_client: OvClient, { signal }: LoadOptions) =>
      loadMessages({ uri, signal }),
    [uri, loadMessages],
  );
  const content = useDashboardQuery(readDocument, requestOptions);
  const messages = useDashboardQuery(readMessages, requestOptions);
  return (
    <ExperienceTrajectoryDetail
      trajectory={trajectory}
      triggerRef={triggerRef}
      content={content.data}
      messages={messages.data}
      contentState={{ ...content, onRetry: content.retry }}
      messagesState={{ ...messages, onRetry: messages.retry }}
      locale={locale}
      onClose={onClose}
      onOpenUri={onOpenUri}
    />
  );
}
