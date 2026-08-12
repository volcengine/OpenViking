<script setup lang="ts">
import { computed, ref } from 'vue'
import { useData } from 'vitepress'

type VikingBotWidgetLocale = 'en' | 'zh'

type VikingBotWidgetMountOptions = {
  site: string
  locale?: VikingBotWidgetLocale
  /**
   * 'none' keeps the widget from rendering its own floating launcher AND from
   * binding a click handler of its own — this component owns the trigger, so a
   * widget-side listener would toggle the panel a second time per click and
   * desync `aria-expanded`.
   */
  trigger?: 'none' | 'floating'
  open?: boolean
  onEvent?: (name: string, payload?: unknown) => void
}

type VikingBotWidgetApi = {
  mount: (options: VikingBotWidgetMountOptions) => void
  open: () => void
  toggle: () => void
}

declare global {
  interface Window {
    VikingBotWidget?: VikingBotWidgetApi
  }
}

const WIDGET_URL =
  import.meta.env.VITE_VIKINGBOT_WIDGET_URL ||
  'https://openviking.net/studio/embed-widget/vikingbot-widget.js'
const WIDGET_SITE = 'vikingbot'

let widgetScriptPromise: Promise<void> | null = null
let widgetMounted = false

const { lang } = useData()
const isOpen = ref(false)
const isLoading = ref(false)
const hasError = ref(false)

const isZh = computed(() => lang.value.startsWith('zh'))
const locale = computed<VikingBotWidgetLocale>(() => (isZh.value ? 'zh' : 'en'))
const copy = computed(() => isZh.value ? {
  trigger: '问问 VikingBot',
  failed: 'VikingBot 暂时无法加载，请稍后重试。'
} : {
  trigger: 'Ask VikingBot',
  failed: 'VikingBot failed to load. Please try again later.'
})

function loadWidgetScript() {
  if (!widgetScriptPromise) {
    widgetScriptPromise = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script')
      script.src = WIDGET_URL
      script.async = true
      script.dataset.vikingbotWidget = ''
      script.addEventListener('load', () => resolve(), { once: true })
      script.addEventListener(
        'error',
        () => {
          script.remove()
          reject(new Error(`Failed to load the VikingBot widget from ${WIDGET_URL}`))
        },
        { once: true }
      )
      document.head.appendChild(script)
    })
  }

  return widgetScriptPromise.catch((error: unknown) => {
    // Let the next click retry a failed load instead of caching the rejection.
    widgetScriptPromise = null
    throw error
  })
}

function onWidgetEvent(name: string) {
  if (name === 'open') isOpen.value = true
  if (name === 'close') isOpen.value = false
}

async function onTriggerClick() {
  if (isLoading.value) return

  hasError.value = false
  isLoading.value = true

  try {
    await loadWidgetScript()

    const widget = window.VikingBotWidget
    if (!widget) throw new Error('The VikingBot widget script did not register window.VikingBotWidget')

    if (widgetMounted) {
      // No local state update here: toggle() emits open/close synchronously and
      // onWidgetEvent is the single writer of isOpen. Flipping it here too
      // would invert aria-expanded on every click.
      widget.toggle()
      return
    }

    widget.mount({
      site: WIDGET_SITE,
      locale: locale.value,
      trigger: 'none',
      open: true,
      onEvent: onWidgetEvent
    })
    widgetMounted = true
  } catch (error) {
    console.error(error)
    hasError.value = true
  } finally {
    isLoading.value = false
  }
}
</script>

<template>
  <span class="vikingbot-launcher">
    <button
      class="vikingbot-trigger"
      type="button"
      :aria-label="copy.trigger"
      :aria-expanded="isOpen"
      :aria-busy="isLoading"
      @click="onTriggerClick"
    >
      <span class="vikingbot-spark" aria-hidden="true">✦</span>
      <span class="vikingbot-trigger-label">{{ copy.trigger }}</span>
    </button>
    <span v-if="hasError" class="vikingbot-trigger-error" role="alert">{{ copy.failed }}</span>
  </span>
</template>
