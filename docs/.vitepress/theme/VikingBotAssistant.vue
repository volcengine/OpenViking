<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'
import { chatWithVikingBot, VikingBotApiError } from './vikingbot-api'
import { renderVikingBotMarkdown } from './vikingbot-markdown'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
}

const { lang } = useData()
const isOpen = ref(false)
const input = ref('')
const loading = ref(false)
const messages = ref<ChatMessage[]>([])
const copiedMessageId = ref('')
const scrollArea = ref<HTMLElement>()
const inputArea = ref<HTMLTextAreaElement>()
const triggerArea = ref<HTMLButtonElement>()
const logoUrl = withBase('/ov-logo.png')
const panelWidth = ref(368)
const preferredPanelWidth = ref(368)
const panelMaxWidth = ref(640)
const isResizing = ref(false)
const PANEL_WIDTH_KEY = 'openviking-vikingbot-panel-width'
const MIN_PANEL_WIDTH = 320
const MAX_PANEL_WIDTH = 640
const MIN_COMPACT_DOC_WIDTH = 400
const MIN_FULL_DOC_WIDTH = 760
const RESIZE_STEP = 16

const isZh = computed(() => lang.value.startsWith('zh'))
const copy = computed(() => isZh.value ? {
  trigger: '问问 VikingBot',
  title: 'VikingBot',
  subtitle: 'OpenViking 文档助手',
  welcome: '你好！我是 VikingBot。你可以问我 OpenViking 的使用方式、核心概念或 API 集成问题。',
  placeholder: '输入你的问题…',
  send: '发送',
  copy: '复制',
  copied: '已复制',
  thinking: '正在查找答案…',
  close: '关闭 VikingBot',
  empty: '请输入问题',
  tooLong: '问题不能超过 500 个字符',
  timeout: '请求超时，请稍后重试。',
  invalid: 'VikingBot 返回了无法识别的响应。',
  failed: '暂时无法连接 VikingBot，请稍后重试。',
  resize: '调整 VikingBot 面板宽度'
} : {
  trigger: 'Ask VikingBot',
  title: 'VikingBot',
  subtitle: 'OpenViking docs assistant',
  welcome: 'Hi! I’m VikingBot. Ask me about OpenViking concepts, usage, or API integration.',
  placeholder: 'Ask a question…',
  send: 'Send',
  copy: 'Copy',
  copied: 'Copied',
  thinking: 'Looking for an answer…',
  close: 'Close VikingBot',
  empty: 'Please enter a question',
  tooLong: 'Questions cannot exceed 500 characters',
  timeout: 'The request timed out. Please try again.',
  invalid: 'VikingBot returned an invalid response.',
  failed: 'VikingBot is unavailable right now. Please try again later.',
  resize: 'Resize VikingBot panel'
})

function openPanel() {
  isOpen.value = true
  nextTick(() => inputArea.value?.focus())
}

function closePanel() {
  const shouldRestoreFocus = isOpen.value
  isOpen.value = false
  if (shouldRestoreFocus) nextTick(() => triggerArea.value?.focus())
}

function maxPanelWidthForViewport() {
  if (window.innerWidth < 768) return MAX_PANEL_WIDTH
  const reservedWidth = window.innerWidth < 1280 ? MIN_COMPACT_DOC_WIDTH : MIN_FULL_DOC_WIDTH
  return Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, window.innerWidth - reservedWidth))
}

function applyPanelWidth(width: number, remember = true) {
  panelMaxWidth.value = maxPanelWidthForViewport()
  panelWidth.value = Math.min(Math.max(width, MIN_PANEL_WIDTH), panelMaxWidth.value)
  if (remember) preferredPanelWidth.value = panelWidth.value
  document.documentElement.style.setProperty('--vikingbot-panel-width', `${panelWidth.value}px`)
}

function onResizeMove(event: PointerEvent) {
  if (!isResizing.value) return
  applyPanelWidth(window.innerWidth - event.clientX)
}

function stopResize() {
  if (!isResizing.value) return
  isResizing.value = false
  document.documentElement.classList.remove('vikingbot-assistant-resizing')
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointercancel', stopResize)
  window.removeEventListener('blur', stopResize)
  localStorage.setItem(PANEL_WIDTH_KEY, String(preferredPanelWidth.value))
}

function startResize(event: PointerEvent) {
  if (window.innerWidth < 768) return
  event.preventDefault()
  isResizing.value = true
  document.documentElement.classList.add('vikingbot-assistant-resizing')
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup', stopResize, { once: true })
  window.addEventListener('pointercancel', stopResize, { once: true })
  window.addEventListener('blur', stopResize, { once: true })
}

function onResizeKeydown(event: KeyboardEvent) {
  let nextWidth: number | undefined
  if (event.key === 'ArrowLeft') nextWidth = panelWidth.value + RESIZE_STEP
  if (event.key === 'ArrowRight') nextWidth = panelWidth.value - RESIZE_STEP
  if (event.key === 'Home') nextWidth = MIN_PANEL_WIDTH
  if (event.key === 'End') nextWidth = panelMaxWidth.value
  if (nextWidth === undefined) return

  event.preventDefault()
  applyPanelWidth(nextWidth)
  localStorage.setItem(PANEL_WIDTH_KEY, String(preferredPanelWidth.value))
}

function onWindowResize() {
  if (window.innerWidth >= 768) applyPanelWidth(preferredPanelWidth.value, false)
}

function errorMessage(error: unknown) {
  if (!(error instanceof VikingBotApiError)) return copy.value.failed
  if (error.code === 'empty_query') return copy.value.empty
  if (error.code === 'query_too_long') return copy.value.tooLong
  if (error.code === 'request_timeout') return copy.value.timeout
  if (error.code === 'invalid_response') return copy.value.invalid
  return copy.value.failed
}

async function copyAnswer(message: ChatMessage) {
  try {
    await navigator.clipboard.writeText(message.content)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = message.content
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
  }

  copiedMessageId.value = message.id
  window.setTimeout(() => {
    if (copiedMessageId.value === message.id) copiedMessageId.value = ''
  }, 1600)
}

async function scrollToBottom() {
  await nextTick()
  scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight, behavior: 'smooth' })
}

async function sendMessage() {
  if (loading.value) return
  const query = input.value.trim()
  if (!query) return

  const userMessage: ChatMessage = {
    id: `${Date.now()}-user`,
    role: 'user',
    content: query
  }
  messages.value.push(userMessage)
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const result = await chatWithVikingBot(query)
    messages.value.push({
      id: `${Date.now()}-assistant`,
      role: 'assistant',
      content: result.text
    })
  } catch (error) {
    messages.value.push({
      id: `${Date.now()}-error`,
      role: 'assistant',
      content: errorMessage(error)
    })
  } finally {
    loading.value = false
    await scrollToBottom()
    inputArea.value?.focus()
  }
}

function onInputKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void sendMessage()
}

function onEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented || !isOpen.value) return
  event.preventDefault()
  closePanel()
}

watch(isOpen, (open) => {
  document.documentElement.classList.toggle('vikingbot-assistant-open', open)
})

onMounted(() => {
  const storedWidth = Number(localStorage.getItem(PANEL_WIDTH_KEY))
  preferredPanelWidth.value = Number.isFinite(storedWidth) && storedWidth > 0 ? storedWidth : 368
  applyPanelWidth(preferredPanelWidth.value, false)
  window.addEventListener('keydown', onEscape)
  window.addEventListener('resize', onWindowResize)
})

onBeforeUnmount(() => {
  document.documentElement.classList.remove('vikingbot-assistant-open')
  document.documentElement.classList.remove('vikingbot-assistant-resizing')
  document.documentElement.style.removeProperty('--vikingbot-panel-width')
  window.removeEventListener('keydown', onEscape)
  window.removeEventListener('resize', onWindowResize)
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  window.removeEventListener('blur', stopResize)
})
</script>

<template>
  <button
    ref="triggerArea"
    class="vikingbot-trigger"
    type="button"
    :aria-label="copy.trigger"
    :aria-expanded="isOpen"
    aria-controls="vikingbot-assistant-panel"
    @click="isOpen ? closePanel() : openPanel()"
  >
    <span class="vikingbot-spark" aria-hidden="true">✦</span>
    <span class="vikingbot-trigger-label">{{ copy.trigger }}</span>
  </button>

  <Teleport to="body">
    <Transition name="vikingbot-panel">
      <aside
        v-if="isOpen"
        id="vikingbot-assistant-panel"
        class="vikingbot-panel"
        aria-label="VikingBot"
      >
        <div
          class="vikingbot-resize-handle"
          role="separator"
          tabindex="0"
          aria-orientation="vertical"
          :aria-label="copy.resize"
          :aria-valuemin="MIN_PANEL_WIDTH"
          :aria-valuemax="panelMaxWidth"
          :aria-valuenow="panelWidth"
          @keydown="onResizeKeydown"
          @pointerdown="startResize"
        />
        <header class="vikingbot-panel-header">
          <div class="vikingbot-identity">
            <span class="vikingbot-avatar" aria-hidden="true">
              <img :src="logoUrl" alt="" />
            </span>
            <span>
              <strong>{{ copy.title }}</strong>
              <small>{{ copy.subtitle }}</small>
            </span>
          </div>
          <button class="vikingbot-close" type="button" :aria-label="copy.close" @click="closePanel">
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div ref="scrollArea" class="vikingbot-messages" aria-live="polite">
          <div class="vikingbot-message is-assistant">
            <span class="vikingbot-message-mark" aria-hidden="true">
              <img :src="logoUrl" alt="" />
            </span>
            <div class="vikingbot-markdown" v-html="renderVikingBotMarkdown(copy.welcome)" />
          </div>
          <div
            v-for="message in messages"
            :key="message.id"
            class="vikingbot-message"
            :class="`is-${message.role}`"
          >
            <span v-if="message.role === 'assistant'" class="vikingbot-message-mark" aria-hidden="true">
              <img :src="logoUrl" alt="" />
            </span>
            <p v-if="message.role === 'user'">{{ message.content }}</p>
            <div v-else class="vikingbot-answer">
              <div
                class="vikingbot-markdown"
                v-html="renderVikingBotMarkdown(message.content)"
              />
              <button
                class="vikingbot-copy-answer"
                type="button"
                @click="copyAnswer(message)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M8 8h10v11H8z" />
                  <path d="M6 16H4V5h10v2" />
                </svg>
                {{ copiedMessageId === message.id ? copy.copied : copy.copy }}
              </button>
            </div>
          </div>
          <div
            v-if="loading"
            class="vikingbot-message is-loading"
            role="status"
            :aria-label="copy.thinking"
          >
            <span class="vikingbot-dots" aria-hidden="true"><i /><i /><i /></span>
          </div>
        </div>

        <form class="vikingbot-composer" @submit.prevent="sendMessage">
          <div class="vikingbot-input-shell">
            <textarea
              ref="inputArea"
              v-model="input"
              :placeholder="copy.placeholder"
              :disabled="loading"
              maxlength="500"
              rows="3"
              @keydown="onInputKeydown"
            />
            <button type="submit" :disabled="loading || !input.trim()" :aria-label="copy.send">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-4.7 14-2.8-5.5L5 12Zm6.5 1.5 3-3" /></svg>
            </button>
          </div>
        </form>
      </aside>
    </Transition>
  </Teleport>
</template>
