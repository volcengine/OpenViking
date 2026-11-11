import { VikingBotMessageList } from '@openviking/react/components/viking-bot'
import '@openviking/react/components/viking-bot/style.css'
import { useTranslation } from 'react-i18next'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { sharedMessages, sharedPart } from '../lib/shared-messages'
import type { Message, MessagePart } from '../lib/types/message'

interface MessageListProps {
  messages: Message[]
  streaming?: { parts?: MessagePart[]; insertIndex?: number; iteration: number }
  onResourceClick?: (uri: string) => void
}

export function MessageList({
  messages,
  streaming,
  onResourceClick,
}: MessageListProps) {
  const { i18n, t } = useTranslation('sessions')
  const source = Array.isArray(messages) ? messages : []
  const index = Math.max(
    0,
    Math.min(streaming?.insertIndex ?? source.length, source.length),
  )
  const before = sharedMessages(source.slice(0, index))
  const after = sharedMessages(source.slice(index))
  return (
    <div className="ov-viking-bot" style={{ height: 'auto', minHeight: 0 }}>
      <VikingBotMessageList
        messages={[...before, ...after]}
        locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        labels={{
          thinking: t('chat.generating'),
          reasoning: t('chat.reasoning'),
          copy: t('chat.copy'),
        }}
        streaming={
          streaming
            ? {
                parts: (streaming.parts ?? []).map(sharedPart),
                insertIndex: before.length,
                iteration: streaming.iteration,
              }
            : undefined
        }
        onOpenUri={onResourceClick}
        onCopy={(text) => {
          void copyTextToClipboard(text)
        }}
      />
    </div>
  )
}
