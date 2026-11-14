import {
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createRequestErrors } from '../../client/error-events.js';
import { LocaleProvider, useLocale } from '../locale-context.js';
import type { Locale } from '../locale.js';
import { useModalFocusShard } from '../primitives/modal/focus-shards.js';
import { Notification } from '../primitives/notification/notification.js';

import { NotificationScopeContext } from './notification-context.js';
import { RequestErrorFeedback } from './request-error-feedback.js';

/** One stack per owning view; nested views share it in the same language/modal. */
export function NotificationScope({
  children,
  locale: localeProp,
  isolateRequests = false,
}: {
  children: ReactNode;
  locale?: Locale;
  isolateRequests?: boolean;
}) {
  const locale = useLocale(localeProp);
  return (
    <ScopeOwner locale={locale} isolateRequests={isolateRequests}>
      {children}
    </ScopeOwner>
  );
}

function ScopeOwner({
  children,
  locale,
  isolateRequests,
}: {
  children: ReactNode;
  locale: Locale;
  isolateRequests: boolean;
}) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const modal = useModalFocusShard();
  const parent = useContext(NotificationScopeContext);
  const [errors] = useState(() =>
    createRequestErrors(isolateRequests ? undefined : parent?.errors.reported),
  );
  const [ownModals] = useState(
    () => new Map<NonNullable<typeof modal>, typeof errors>(),
  );
  const modals = (!isolateRequests && parent?.modals) || ownModals;
  const parentModal = parent?.modal;
  // Effect necessity: request handlers may belong to the surrounding block while
  // running inside a portaled Modal. Route feedback to the open Modal's stack so
  // its actions stay within FocusLock; unregister when that Modal unmounts.
  useEffect(() => {
    if (!modal || modal === parentModal) return;
    modals.set(modal, errors);
    return () => {
      modals.delete(modal);
    };
  }, [errors, modal, modals, parentModal]);
  const publish = useMemo(
    () => (failure: Parameters<typeof errors.publish>[0]) => {
      const target = [...modals.values()].at(-1) ?? errors;
      target.publish(failure);
    },
    [errors, modals],
  );
  const inherit = parent?.locale === locale && parent.modal === modal;
  const dismiss = useMemo(
    () => (error: unknown) => {
      errors.dismissError(error);
      for (const target of modals.values()) target.dismissError(error);
    },
    [errors, modals],
  );
  const activeContainer = inherit ? parent.container : container;
  const value = useMemo(
    () => ({
      container: activeContainer,
      locale,
      modal,
      errors,
      modals,
      publish,
      dismiss,
    }),
    [activeContainer, locale, modal, errors, modals, publish, dismiss],
  );
  return (
    <LocaleProvider locale={locale}>
      <NotificationScopeContext.Provider value={value}>
        {children}
        <RequestErrorFeedback errors={errors} />
        {inherit ? null : (
          <Notification
            notices={[]}
            onRemove={() => {}}
            locale={locale}
            contentRef={setContainer}
          />
        )}
      </NotificationScopeContext.Provider>
    </LocaleProvider>
  );
}
