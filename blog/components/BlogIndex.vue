<script setup lang="ts">
import type { Post } from 'valaxy'
import { usePageList, useValaxyI18n } from 'valaxy'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { resolveBlogLocale } from '../config/locales'

const pages = usePageList()
const { $tO } = useValaxyI18n()
const route = useRoute()

const currentLocale = computed(() => resolveBlogLocale(route.path))

const posts = computed(() =>
  pages.value.filter((post) => {
    if (!post.path || !post.date || post.path.endsWith('/'))
      return false

    return post.path.startsWith(currentLocale.value.postsPrefix)
  }),
)

const hero = computed(() => currentLocale.value.hero)

function coverFor(post: Post) {
  return post.cover || post.firstImage || '/covers/openviking-banner.jpg'
}

function categoryText(post: Post) {
  const categories = post.categories
  if (Array.isArray(categories))
    return categories.join(' / ')
  return categories || hero.value.fallbackCategory
}

function formatDate(value?: string | number | Date) {
  if (!value)
    return ''

  return new Date(value).toLocaleDateString(hero.value.dateLocale, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  })
}
</script>

<template>
  <main class="blog-index">
    <section class="blog-hero">
      <p class="blog-kicker">
        {{ hero.kicker }}
      </p>
      <h1>{{ hero.title }}</h1>
      <p class="blog-intro">
        {{ hero.intro }}
      </p>
    </section>

    <section class="post-list" :aria-label="hero.label">
      <article v-for="post in posts" :key="post.path" class="post-card">
        <RouterLink class="post-cover-link" :to="post.path || ''" :aria-label="$tO(post.title)">
          <img class="post-cover" :src="coverFor(post)" :alt="$tO(post.title)" loading="lazy">
        </RouterLink>

        <div class="post-body">
          <div class="post-meta">
            <span>{{ categoryText(post) }}</span>
            <time v-if="formatDate(post.date)" :datetime="String(post.date)">
              {{ formatDate(post.date) }}
            </time>
          </div>

          <h2>
            <RouterLink :to="post.path || ''">
              {{ $tO(post.title) }}
            </RouterLink>
          </h2>

          <p v-if="post.excerpt" class="post-excerpt">
            {{ $tO(post.excerpt) }}
          </p>

          <div v-if="post.tags?.length" class="post-tags" aria-label="Tags">
            <span v-for="tag in post.tags.slice(0, 3)" :key="tag">
              {{ tag }}
            </span>
          </div>
        </div>
      </article>
    </section>
  </main>
</template>

<style scoped>
.blog-index {
  width: min(1080px, calc(100% - 48px));
  margin: 0 auto;
  padding: 72px 0 88px;
}

.blog-hero {
  max-width: 720px;
  margin-bottom: 48px;
}

.blog-kicker {
  margin: 0 0 14px;
  color: var(--va-c-primary);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.blog-hero h1 {
  margin: 0;
  color: var(--va-c-text);
  font-size: clamp(44px, 6vw, 72px);
  font-weight: 850;
  line-height: 0.98;
  letter-spacing: 0;
}

.blog-intro {
  max-width: 620px;
  margin: 22px 0 0;
  color: var(--va-c-text-light);
  font-size: 18px;
  line-height: 1.7;
}

.post-list {
  display: grid;
  gap: 20px;
}

.post-card {
  display: grid;
  grid-template-columns: minmax(220px, 36%) 1fr;
  min-height: 232px;
  overflow: hidden;
  border: 1px solid rgba(15, 118, 110, 0.14);
  border-radius: 8px;
  background: color-mix(in srgb, var(--va-c-bg-light) 92%, white);
  box-shadow: 0 18px 50px rgba(23, 33, 31, 0.08);
}

.post-cover-link {
  display: block;
  min-height: 100%;
  overflow: hidden;
  background: #17211f;
}

.post-cover {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 232px;
  object-fit: cover;
  transition: transform 240ms ease;
}

.post-card:hover .post-cover {
  transform: scale(1.035);
}

.post-body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  padding: 30px 34px;
}

.post-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  color: var(--va-c-text-light);
  font-size: 13px;
  font-weight: 700;
}

.post-meta :deep(time) {
  color: var(--va-c-text-light);
  font-size: 13px;
}

.post-body h2 {
  margin: 12px 0 0;
  font-size: clamp(25px, 3vw, 38px);
  font-weight: 820;
  line-height: 1.08;
  letter-spacing: 0;
}

.post-body h2 a {
  color: var(--va-c-text);
  text-decoration: none;
  border-bottom: 0;
}

.post-body h2 a:hover {
  color: var(--va-c-primary-dark);
}

.post-excerpt {
  margin: 16px 0 0;
  color: var(--va-c-text-light);
  font-size: 16px;
  line-height: 1.65;
}

.post-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 22px;
}

.post-tags span {
  border: 1px solid rgba(15, 118, 110, 0.22);
  border-radius: 999px;
  padding: 4px 10px;
  color: var(--va-c-primary-dark);
  background: rgba(20, 184, 166, 0.08);
  font-size: 12px;
  font-weight: 700;
}

html.dark .post-card {
  border-color: rgba(94, 234, 212, 0.18);
  background: #161f1d;
  box-shadow: none;
}

html.dark .post-body h2 a:hover {
  color: var(--va-c-primary);
}

html.dark .post-tags span {
  color: var(--va-c-primary);
  background: rgba(94, 234, 212, 0.09);
}

@media (max-width: 760px) {
  .blog-index {
    width: min(100% - 28px, 1080px);
    padding: 42px 0 64px;
  }

  .blog-hero {
    margin-bottom: 30px;
  }

  .post-card {
    grid-template-columns: 1fr;
  }

  .post-cover-link,
  .post-cover {
    aspect-ratio: 16 / 9;
    min-height: 0;
  }

  .post-body {
    padding: 24px;
  }
}
</style>
