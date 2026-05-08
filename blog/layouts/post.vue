<script setup lang="ts">
import type { Post } from 'valaxy'
import { useFrontmatter, useSiteStore, useValaxyI18n } from 'valaxy'
import { computed, nextTick, onMounted, onUnmounted, ref, watchEffect } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const site = useSiteStore()
const frontmatter = useFrontmatter()
const { $tO } = useValaxyI18n()
const lastUpdated = ref('')
const postTitle = computed(() => $tO(frontmatter.value.title))
const postCover = computed(() => frontmatter.value.cover as string | undefined)

const posts = computed(() => site.postList.filter(post => post.path && !post.path.endsWith('/')))
const currentIndex = computed(() => posts.value.findIndex(post => post.path === route.path))
const previousPost = computed<Post | null>(() => {
  const index = currentIndex.value
  return index >= 0 && index < posts.value.length - 1 ? posts.value[index + 1] : null
})
const nextPost = computed<Post | null>(() => {
  const index = currentIndex.value
  return index > 0 ? posts.value[index - 1] : null
})

function removeInjectedCover() {
  document.querySelector('html.blog-post-detail .blog-post-cover')?.remove()
}

function syncInjectedCover() {
  const cover = postCover.value

  removeInjectedCover()

  if (!cover)
    return

  void nextTick(() => {
    window.requestAnimationFrame(() => {
      const title = document.querySelector<HTMLHeadingElement>('html.blog-post-detail .markdown-body > h1:first-of-type')

      if (!title)
        return

      const figure = document.createElement('figure')
      figure.className = 'blog-post-cover'

      const image = document.createElement('img')
      image.src = cover
      image.alt = postTitle.value || ''

      figure.appendChild(image)
      title.insertAdjacentElement('afterend', figure)
    })
  })
}

onMounted(() => {
  document.documentElement.classList.add('blog-post-detail')
  watchEffect(() => {
    const value = frontmatter.value.updated || frontmatter.value.date
    lastUpdated.value = value
      ? new Date(value).toLocaleString(window.navigator.language, {
          year: 'numeric',
          month: 'numeric',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })
      : ''

    syncInjectedCover()
  })
})

onUnmounted(() => {
  removeInjectedCover()
  document.documentElement.classList.remove('blog-post-detail')
})
</script>

<template>
  <Layout>
    <template #main-content-after>
      <footer class="blog-post-footer">
        <p v-if="lastUpdated" class="blog-post-updated">
          Last updated: {{ lastUpdated }}
        </p>

        <nav
          v-if="previousPost || nextPost"
          class="blog-post-pager"
          aria-label="Blog post pager"
        >
          <RouterLink
            v-if="previousPost"
            class="blog-post-pager-card previous"
            :to="previousPost.path || ''"
          >
            <span class="pager-label">上一篇</span>
            <span class="pager-title">{{ $tO(previousPost.title) }}</span>
          </RouterLink>
          <div v-else />

          <RouterLink
            v-if="nextPost"
            class="blog-post-pager-card next"
            :to="nextPost.path || ''"
          >
            <span class="pager-label">下一篇</span>
            <span class="pager-title">{{ $tO(nextPost.title) }}</span>
          </RouterLink>
          <div v-else />
        </nav>
      </footer>
    </template>
  </Layout>
</template>

<style>
html.blog-post-detail {
  color-scheme: dark;
  --va-c-bg: #0f0f0f;
  --va-c-bg-alt: #141414;
  --va-c-bg-soft: #1a1a1a;
  --va-c-bg-mute: #050505;
  --va-c-divider: #2b2d31;
  --va-c-text: #edf2ef;
  --va-c-text-light: #b9c2bd;
  --va-c-text-lighter: #7e8791;
  --va-c-brand: #8fb7ff;
  --va-c-primary: #8fb7ff;
  --pr-c-text-1: #edf2ef;
  --pr-c-text-2: #b9c2bd;
  --pr-aside-divider: #2b2d31;
  --pr-aside-text-1: #edf2ef;
  --pr-aside-text-2: #9aa4ad;
}

html.blog-post-detail,
html.blog-post-detail body,
html.blog-post-detail .layout,
html.blog-post-detail .press-main,
html.blog-post-detail .aside-container {
  background: #0f0f0f;
}

html.blog-post-detail .press-main {
  padding-top: 52px;
}

html.blog-post-detail .press-main > .relative {
  padding-top: 0;
}

html.blog-post-detail .press-main .container {
  position: relative;
  display: grid !important;
  grid-template-columns: minmax(0, 1fr) minmax(0, 860px) minmax(300px, 1fr);
  justify-content: center;
  align-items: start;
  width: min(1500px, calc(100vw - 96px));
  margin: 0 auto;
  gap: 0;
}

html.blog-post-detail .vp-doc.content {
  grid-column: 2;
  width: 100%;
  max-width: 860px;
  margin: 0;
}

html.blog-post-detail .markdown-body > div:first-child:has(> h1) {
  order: 2;
  display: flex;
  justify-content: flex-end;
  width: 100%;
  margin: 0 0 24px;
}

html.blog-post-detail .markdown-body > div:first-child:has(> h1) > h1 {
  display: none;
}

html.blog-post-detail .press-post-actions {
  border-color: #34373d;
  border-radius: 8px;
  background: #17181b;
}

html.blog-post-detail .press-post-actions-main,
html.blog-post-detail .press-post-actions-trigger {
  min-height: 40px;
  background: #17181b;
  color: #aeb6bf;
  font-size: 14px;
}

html.blog-post-detail .press-post-actions-main {
  min-width: 0;
  flex: 1;
  gap: 10px;
  padding: 0 14px;
}

html.blog-post-detail .press-post-actions-trigger {
  min-width: 42px;
}

html.blog-post-detail .press-post-actions-main:hover,
html.blog-post-detail .press-post-actions-trigger:hover {
  background: #1f2227;
  color: #edf2ef;
}

html.blog-post-detail .markdown-body {
  display: flex;
  flex-direction: column;
  max-width: 860px;
  color: #e6ece8;
}

html.blog-post-detail .markdown-body > * {
  order: 10;
}

html.blog-post-detail .markdown-body h1 {
  order: 1;
  max-width: 720px;
  margin: 0 0 24px;
  color: #f4f7f5;
  font-size: clamp(34px, 3.2vw, 46px);
  font-weight: 820;
  line-height: 1.12;
  letter-spacing: 0;
}

html.blog-post-detail .blog-post-cover {
  order: 3;
  overflow: hidden;
  width: 100%;
  margin: 0 0 34px;
  border: 1px solid #24272d;
  border-radius: 8px;
  background: #070707;
  aspect-ratio: 16 / 6;
}

html.blog-post-detail .blog-post-cover img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

html.blog-post-detail .markdown-body h2 {
  margin-top: 46px;
  padding-top: 28px;
  border-top: 1px solid #2b2d31;
  color: #f4f7f5;
  font-size: clamp(24px, 2.4vw, 30px);
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body h3 {
  margin-top: 30px;
  color: #f4f7f5;
  font-size: 21px;
  line-height: 1.25;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body p,
html.blog-post-detail .markdown-body li {
  color: #d8dfdb;
  font-size: 17px;
  line-height: 1.72;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body ul {
  padding-left: 1.3em;
}

html.blog-post-detail .markdown-body strong {
  color: #f4f7f5;
}

html.blog-post-detail .markdown-body a {
  color: #9fc2ff;
}

html.blog-post-detail .markdown-body div[class*='language-'] {
  overflow: hidden;
  border: 1px solid #181a1f;
  border-radius: 8px;
  background: #050505;
}

html.blog-post-detail .markdown-body pre {
  background: #050505;
  font-size: 14px;
}

html.blog-post-detail .press-aside {
  grid-column: 3;
  width: 260px;
  margin-left: 80px;
  padding-left: 0;
}

html.blog-post-detail .aside-container {
  margin-top: 0;
  padding-top: 72px;
}

html.blog-post-detail .aside-content {
  align-items: stretch;
}

html.blog-post-detail .press-aside .content {
  width: 240px;
  padding-left: 22px;
  border-left-color: #2b2d31;
  font-size: 15px;
}

html.blog-post-detail .press-aside .outline-title {
  margin-bottom: 16px;
  color: #f4f7f5;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0;
}

html.blog-post-detail .press-aside .outline-link {
  color: #aeb6bf;
  font-size: 15px;
  line-height: 1.9;
}

html.blog-post-detail .press-aside .outline-link:hover,
html.blog-post-detail .press-aside .outline-link.active {
  color: #f4f7f5;
}

html.blog-post-detail .press-footer {
  border-top-color: #181a1f;
  background: #0f0f0f;
}

html.blog-post-detail .press-doc-footer {
  display: none;
}

html.blog-post-detail .blog-post-footer {
  width: 100%;
  max-width: 860px;
  margin: 72px 0 0;
  padding-bottom: 56px;
}

html.blog-post-detail .blog-post-updated {
  margin: 0 0 28px;
  color: #b9c2bd;
  font-size: 16px;
  line-height: 1.6;
}

html.blog-post-detail .blog-post-pager {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
  padding-top: 28px;
  border-top: 1px solid #2b2d31;
}

html.blog-post-detail .blog-post-pager-card {
  display: flex;
  min-height: 92px;
  flex-direction: column;
  justify-content: center;
  border: 1px solid #2b2d31;
  border-radius: 8px;
  padding: 20px 24px;
  color: #edf2ef;
  text-decoration: none;
  transition: border-color 180ms ease, background 180ms ease;
}

html.blog-post-detail .blog-post-pager-card:hover {
  border-color: #5eead4;
  background: #141716;
}

html.blog-post-detail .blog-post-pager-card.next {
  text-align: right;
}

html.blog-post-detail .pager-label {
  margin-bottom: 8px;
  color: #aeb6bf;
  font-size: 14px;
  font-weight: 650;
  line-height: 1.2;
}

html.blog-post-detail .pager-title {
  color: #5eead4;
  font-size: 16px;
  font-weight: 750;
  line-height: 1.35;
}

@media (max-width: 1279px) {
  html.blog-post-detail .press-main .container {
    grid-template-columns: minmax(0, 920px);
    justify-content: center;
    width: min(900px, calc(100vw - 48px));
  }

  html.blog-post-detail .vp-doc.content {
    grid-column: 1;
    max-width: 100%;
  }

  html.blog-post-detail .press-aside {
    grid-column: 1;
    margin-left: 0;
  }

  html.blog-post-detail .markdown-body > div:first-child:has(> h1) {
    justify-content: flex-end;
    margin: 0 0 28px;
  }
}

@media (max-width: 760px) {
  html.blog-post-detail .press-main {
    padding-top: 32px;
  }

  html.blog-post-detail .press-main .container {
    width: min(100vw - 28px, 900px);
  }

  html.blog-post-detail .markdown-body > div:first-child:has(> h1) {
    justify-content: flex-start;
  }

  html.blog-post-detail .blog-post-cover {
    aspect-ratio: 16 / 8;
    margin-bottom: 28px;
  }

  html.blog-post-detail .markdown-body h1 {
    font-size: 32px;
  }

  html.blog-post-detail .markdown-body p,
  html.blog-post-detail .markdown-body li {
    font-size: 16px;
  }

  html.blog-post-detail .blog-post-pager {
    grid-template-columns: 1fr;
  }

  html.blog-post-detail .blog-post-pager-card.next {
    text-align: left;
  }
}
</style>
