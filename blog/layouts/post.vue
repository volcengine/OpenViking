<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'

onMounted(() => {
  document.documentElement.classList.add('blog-post-detail')
})

onUnmounted(() => {
  document.documentElement.classList.remove('blog-post-detail')
})
</script>

<template>
  <Layout />
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
  display: grid !important;
  grid-template-columns: minmax(0, 920px) 260px;
  justify-content: center;
  align-items: start;
  width: min(1320px, calc(100vw - 96px));
  margin: 0 auto;
  gap: 80px;
}

html.blog-post-detail .vp-doc.content {
  width: 100%;
  max-width: 920px;
  margin: 0;
}

html.blog-post-detail .markdown-body > div:first-child:has(> h1) {
  display: flex;
  justify-content: flex-end;
  margin: 0 0 36px;
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
  min-height: 42px;
  background: #17181b;
  color: #aeb6bf;
  font-size: 15px;
}

html.blog-post-detail .press-post-actions-main {
  gap: 10px;
  padding: 0 18px;
}

html.blog-post-detail .press-post-actions-trigger {
  min-width: 48px;
}

html.blog-post-detail .press-post-actions-main:hover,
html.blog-post-detail .press-post-actions-trigger:hover {
  background: #1f2227;
  color: #edf2ef;
}

html.blog-post-detail .markdown-body {
  max-width: 920px;
  color: #e6ece8;
}

html.blog-post-detail .markdown-body h1 {
  max-width: 760px;
  margin: 0 0 28px;
  color: #f4f7f5;
  font-size: clamp(40px, 4vw, 56px);
  font-weight: 850;
  line-height: 1.08;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body h2 {
  margin-top: 52px;
  padding-top: 34px;
  border-top: 1px solid #2b2d31;
  color: #f4f7f5;
  font-size: clamp(28px, 3vw, 36px);
  font-weight: 820;
  line-height: 1.15;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body h3 {
  margin-top: 34px;
  color: #f4f7f5;
  font-size: 24px;
  line-height: 1.25;
  letter-spacing: 0;
}

html.blog-post-detail .markdown-body p,
html.blog-post-detail .markdown-body li {
  color: #d8dfdb;
  font-size: 19px;
  line-height: 1.78;
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
  font-size: 16px;
}

html.blog-post-detail .press-aside {
  width: 260px;
  padding-left: 0;
}

html.blog-post-detail .aside-container {
  margin-top: 0;
  padding-top: 88px;
}

html.blog-post-detail .aside-content {
  align-items: stretch;
}

html.blog-post-detail .press-aside .content {
  width: 240px;
  padding-left: 22px;
  border-left-color: #2b2d31;
  font-size: 17px;
}

html.blog-post-detail .press-aside .outline-title {
  margin-bottom: 16px;
  color: #f4f7f5;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0;
}

html.blog-post-detail .press-aside .outline-link {
  color: #aeb6bf;
  font-size: 17px;
  line-height: 2.05;
}

html.blog-post-detail .press-aside .outline-link:hover,
html.blog-post-detail .press-aside .outline-link.active {
  color: #f4f7f5;
}

html.blog-post-detail .press-footer {
  border-top-color: #181a1f;
  background: #0f0f0f;
}

@media (max-width: 1279px) {
  html.blog-post-detail .press-main .container {
    grid-template-columns: minmax(0, 920px);
    justify-content: center;
    width: min(900px, calc(100vw - 48px));
  }

  html.blog-post-detail .vp-doc.content {
    max-width: 100%;
  }
}

@media (max-width: 760px) {
  html.blog-post-detail .press-main {
    padding-top: 32px;
  }

  html.blog-post-detail .press-main .container {
    width: min(100vw - 28px, 900px);
  }

  html.blog-post-detail .blog-post-action-row {
    justify-content: flex-start;
    margin-bottom: 28px;
  }

  html.blog-post-detail .markdown-body h1 {
    font-size: 36px;
  }

  html.blog-post-detail .markdown-body p,
  html.blog-post-detail .markdown-body li {
    font-size: 17px;
  }
}
</style>
