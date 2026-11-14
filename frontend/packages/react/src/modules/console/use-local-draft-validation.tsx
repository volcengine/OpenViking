import { useState } from 'react';
import { OvClientError } from '../../client/errors.js';
import type { VikingBotDraft } from '../../components/viking-bot/model.js';
import type { Locale } from '../../shared/locale.js';
import { localizeBotError } from './chat/errors.js';
import { nativeChatInput } from './chat/native-input.js';
import { ConversationFeedback } from './conversation-feedback.js';

/** Validate the native payload before clearing the composer or creating a session. */
export function useLocalDraftValidation(enabled: boolean, locale: Locale) {
  const [failure, setFailure] = useState<OvClientError>();
  return {
    validate: (draft: VikingBotDraft) => {
      if (!enabled) return true;
      try {
        nativeChatInput({ sessionId: '', ...draft });
        setFailure(undefined);
        return true;
      } catch (error) {
        if (
          !(error instanceof OvClientError) ||
          error.code !== 'BOT_INVALID_ATTACHMENT'
        )
          throw error;
        setFailure(error);
        return false;
      }
    },
    feedback: failure ? (
      <ConversationFeedback
        error={localizeBotError(failure, locale)!}
        title=""
        locale={locale}
      />
    ) : undefined,
  };
}
