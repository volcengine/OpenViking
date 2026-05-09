<script setup lang="ts">
import { useData, withBase } from 'vitepress'
import { computed } from 'vue'

const { page } = useData()

const llmsUrl = computed(() => {
  const pagePath = page.value.relativePath.replace(/\.md$/, '')
  return withBase(`/${pagePath}/llms.txt`)
})

const isDoc = computed(() => page.value.relativePath !== 'index.md')
</script>

<template>
  <div v-if="isDoc" class="llms-link-wrap">
    <a :href="llmsUrl" target="_blank" rel="noopener" class="llms-link">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
        <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
      </svg>
      llms.txt
    </a>
  </div>
</template>

<style scoped>
.llms-link-wrap {
  display: inline-flex;
  margin-bottom: 8px;
}

.llms-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--vp-c-text-2);
  font-size: 13px;
  text-decoration: none;
  transition: color 0.2s;
}

.llms-link:hover {
  color: var(--vp-c-brand-1);
}
</style>
