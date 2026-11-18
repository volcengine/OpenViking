import type { Locale } from '../../shared/locale.js';
import { Button } from '../../shared/primitives/button/button.js';
import { VikingBotMessageListLoading } from '../../components/viking-bot/viking-bot-message-list.js';
import type { VikingBotChatResult } from './chat/types.js';
import feedbackEn from './locales/feedback.en-US.js';
import feedbackZh from './locales/feedback.zh-CN.js';

export function renderConversationHistory(
  chat: VikingBotChatResult,
  switching: boolean,
  locale: Locale,
) {
  const copy = locale === 'zh-CN' ? feedbackZh : feedbackEn;
  if (switching || (chat.status === 'loading' && !chat.messages.length)) {
    return <VikingBotMessageListLoading label={copy.historyLoading} />;
  }
  if (chat.status === 'error' && chat.sessionId && !chat.messages.length) {
    return (
      <div className="ov-console__history-state">
        {copy.historyFailed}
        <Button
          htmlType="button"
          size="small"
          type="text"
          onClick={() => void chat.reload()}
        >
          {copy.retryHistory}
        </Button>
      </div>
    );
  }
}
