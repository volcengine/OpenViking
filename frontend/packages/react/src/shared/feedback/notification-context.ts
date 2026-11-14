import { createContext } from 'react';
import type {
  RequestErrors,
  RequestFailure,
} from '../../client/error-events.js';
import type { Locale } from '../locale.js';
import type { useModalFocusShard } from '../primitives/modal/focus-shards.js';

type Scope = {
  container: HTMLElement | null;
  locale: Locale;
  modal: ReturnType<typeof useModalFocusShard>;
  errors: RequestErrors;
  modals: Map<NonNullable<Scope['modal']>, RequestErrors>;
  publish: (failure: Omit<RequestFailure, 'id'>) => void;
  dismiss: (error: unknown) => void;
};

export const NotificationScopeContext = createContext<Scope | null>(null);
