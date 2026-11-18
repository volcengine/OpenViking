import { useState } from 'react';
import type { RefObject } from 'react';
import Markdown from 'react-markdown';
import { ArrowUpRightIcon, FileLinkIcon } from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { ExperienceSourceMessages } from './experience-source-messages.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import { experienceLabels, trajectoryDetailLabels } from './labels.js';
import { ExperienceQueryState } from './experience-state.js';
import { ExperienceCopyAction } from './experience-copy-action.js';
import type {
  ExperienceDashboardState,
  ExperienceTrajectory,
  ExperienceTrajectoryMessages,
} from './types.js';

export function ExperienceTrajectoryDetail({
  trajectory,
  triggerRef,
  content,
  messages,
  contentState,
  messagesState,
  locale: localeProp,
  onClose,
  onOpenUri,
}: {
  trajectory: ExperienceTrajectory;
  triggerRef: RefObject<HTMLButtonElement | null>;
  content?: string;
  messages?: ExperienceTrajectoryMessages;
  contentState: ExperienceDashboardState;
  messagesState: ExperienceDashboardState;
  locale?: Locale;
  onClose: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const locale = useLocale(localeProp);
  const copy = trajectoryDetailLabels(locale);
  const t = experienceLabels(locale);
  const [raw, setRaw] = useState(false);
  const slash = trajectory.uri.lastIndexOf('/') + 1;
  const prefix = trajectory.uri.slice(0, slash);
  const name = trajectory.uri.slice(slash);
  const query = { loading: t.loading, failed: t.failed, retry: t.retry };
  return (
    <Modal
      open
      title={t.trajectoryDetails}
      className="ov-experience-trajectory-detail"
      triggerRef={triggerRef}
      locale={locale}
      onClose={onClose}
    >
      <header className="ov-experience-trajectory-detail__header">
        <FileLinkIcon aria-hidden="true" width={12} height={12} />
        <h2 title={trajectory.uri}>
          <span>{prefix}</span>
          <strong>{name}</strong>
        </h2>
        <ExperienceCopyAction value={trajectory.uri} labels={t} />
        {onOpenUri ? (
          <Tooltip content={t.open} placement="top">
            <button
              type="button"
              className="ov-experience-dashboard__icon-button"
              aria-label={t.open}
              onClick={() => {
                onClose();
                onOpenUri(trajectory.uri);
              }}
            >
              <ArrowUpRightIcon width={12} height={12} />
            </button>
          </Tooltip>
        ) : null}
      </header>
      <div className="ov-experience-trajectory-detail__columns">
        <section
          className="ov-experience-trajectory-detail__document"
          aria-label={copy.body}
        >
          <div className="ov-experience-trajectory-detail__frame">
            <div className="ov-experience-trajectory-detail__toolbar">
              <div className="ov-experience-trajectory-detail__modes">
                <button
                  type="button"
                  aria-pressed={!raw}
                  onClick={() => setRaw(false)}
                >
                  {copy.preview}
                </button>
                <button
                  type="button"
                  aria-pressed={raw}
                  onClick={() => setRaw(true)}
                >
                  Markdown
                </button>
              </div>
              <ExperienceCopyAction value={content} labels={t} labelled />
            </div>
            <div className="ov-experience-trajectory-detail__document-body">
              {content ? (
                raw ? (
                  <pre>{content}</pre>
                ) : (
                  <Markdown skipHtml disallowedElements={['img']}>
                    {content}
                  </Markdown>
                )
              ) : (
                <ExperienceQueryState
                  {...query}
                  state={contentState}
                  rows={4}
                  empty={copy.emptyBody}
                />
              )}
            </div>
          </div>
        </section>
        <section
          className="ov-experience-trajectory-detail__message-panel"
          aria-label={copy.messages}
        >
          <header className="ov-experience-trajectory-detail__message-heading">
            <h3>{copy.messages}</h3>
            {messages?.archiveUri ? (
              <p title={messages.archiveUri}>
                <code>{messages.archiveUri}</code>
              </p>
            ) : null}
          </header>
          <div className="ov-experience-trajectory-detail__message-viewport">
            {messages?.items.length ? (
              <ExperienceSourceMessages
                items={messages.items}
                locale={locale}
                unknown={t.unknown}
              />
            ) : (
              <ExperienceQueryState
                {...query}
                state={messagesState}
                rows={6}
                empty={copy.emptyMessages}
              />
            )}
          </div>
        </section>
      </div>
    </Modal>
  );
}
