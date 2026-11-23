<script setup lang="ts">
import { computed } from 'vue'
import { useData } from 'vitepress'
const { isDark, lang } = useData()
const label = computed(() => lang.value.startsWith('zh')
  ? (isDark.value ? '切换到浅色模式' : '切换到深色模式')
  : (isDark.value ? 'Switch to light mode' : 'Switch to dark mode'))
</script>
<template>
  <button class="ov-mode-toggle" type="button" :aria-label="label" :title="label" @click="isDark = !isDark">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <template v-if="isDark"><circle cx="12" cy="12" r="5"/><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72 1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></template>
      <path v-else d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  </button>
</template>
