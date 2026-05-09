import { h } from 'vue'
import DefaultTheme from 'vitepress/theme'
import CopyMarkdownButton from './CopyMarkdownButton.vue'
import LlmsTxtLink from './LlmsTxtLink.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'doc-before': () => h(CopyMarkdownButton),
      'doc-footer-before': () => h(LlmsTxtLink)
    })
  }
}
