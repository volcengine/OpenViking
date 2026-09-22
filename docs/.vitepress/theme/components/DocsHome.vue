<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useData, withBase } from 'vitepress'
import SideRays from './SideRays.vue'
import { data as catalog } from '../docs-catalog.data'
import './docs-home.css'

const { lang, isDark } = useData()
const zh = computed(() => lang.value.startsWith('zh'))
const locale = computed(() => zh.value ? 'zh' : 'en')
const t = (en, cn) => zh.value ? cn : en
const link = path => withBase(`/${locale.value}/${path}`)
const selected = ref('resources')
const filter = ref('')
const expanded = ref(new Set(['getting-started']))
const copied = ref(false)
const copyError = ref(false)
let copyTimer
function openSection(id) {
  if (id.startsWith('section-')) expanded.value = new Set([...expanded.value, id.slice(8)])
}
function openHashSection() { openSection(window.location.hash.slice(1)) }
function toggleSection(id, event) {
  if (filter.value) return
  const next = new Set(expanded.value)
  if (event.target.open) next.add(id)
  else next.delete(id)
  expanded.value = next
}
onMounted(() => { openHashSection(); window.addEventListener('hashchange', openHashSection) })
onBeforeUnmount(() => { clearTimeout(copyTimer); window.removeEventListener('hashchange', openHashSection) })
const install = 'npm install -g @openviking/cli'
async function copyInstall() {
  try {
    await navigator.clipboard.writeText(install)
    copied.value = true; copyError.value = false
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 2200)
  } catch { copyError.value = true }
}
const context = computed(() => ({
  resources: { name: 'resources/', files: ['project/', '  .abstract.md', '  .overview.md', '  architecture.md'], tag: t('External knowledge', '外部知识') },
  memories: { name: 'user/alice/memories/', files: ['preferences/', '  writing-style.md', 'experiences/', '  debugging.md'], tag: t('Retained experience', '保留经验') },
  agent: { name: 'agent/', files: ['skills/', '  code-review/', '    SKILL.md', 'rules/', '  coding.md'], tag: t('Skills & rules', '技能与规则') }
}[selected.value]))
const atlas = computed(() => catalog[locale.value].map(section => ({ ...section,
  pages: section.pages.filter(page => `${section[locale.value]} ${section.id} ${page.title} ${page.file} ${page.group}`.toLowerCase().includes(filter.value.trim().toLowerCase()))
})).filter(section => section.pages.length))
const total = computed(() => catalog[locale.value].reduce((n, section) => n + section.pages.length, 0))
const shown = computed(() => atlas.value.reduce((n, section) => n + section.pages.length, 0))
const paths = computed(() => [
  { label: t('RETRIEVAL', '检索'), icon: '↗', title: t('Run your first retrieval', '完成第一次检索'), description: t('Connect to a service, import a document, and find what matters.', '连接服务，导入文档，找到所需的上下文。'), href: 'getting-started/02-quickstart', action: t('Start building', '开始使用'), primary: true },
  { label: 'AGENT', icon: '⌘', title: t('Connect your agent', '接入你的 Agent'), description: t('Add cross-session memory to the tools you already use.', '为正在使用的工具加入跨会话记忆。'), href: 'agent-integrations/01-overview', action: t('Explore integrations', '查看集成') },
  { label: 'API', icon: '{}', title: t('Build with the API', '通过 API 构建'), description: t('Work with resources, sessions, and retrieval from your application.', '在应用中管理资源、会话和检索。'), href: 'api/01-overview', action: t('Open API reference', '查看 API') }
])
const agents = [
  ['Claude Code', '02-claude-code'],
  ['Codex', '04-codex'],
  ['Cursor', '12-cursor'],
  ['Hermes', '05-hermes'],
  ['OpenCode', '10-opencode']
]
</script>

<template>
  <div class="docs-home">
    <section class="home-hero" aria-labelledby="home-title">
      <div class="hero-light" aria-hidden="true">
        <SideRays :speed="0.65" :ray-color1="isDark ? '#e7b967' : '#a16207'" :ray-color2="isDark ? '#78aaff' : '#1d4ed8'" :intensity="isDark ? 1.8 : 1.15" :spread="2" :tilt="-8" :saturation="isDark ? 1.1 : 0.9" :blend="0.55" :falloff="1.6" :opacity="isDark ? 0.85 : 0.8" />
      </div>
      <div class="home-shell hero-inner">
        <div class="hero-copy">
          <p class="eyebrow"><span class="status-dot" /> OPENVIKING <span class="eyebrow-separator">/</span> {{ t('DOCUMENTATION', '开发者文档') }}</p>
          <h1 id="home-title">{{ t('Context worth', '让上下文，') }}<br><em>{{ t('keeping.', '掌握在你自己手里') }}</em></h1>
          <p class="hero-description">{{ t('The context database for AI agents.', '面向 AI Agent 的上下文数据库。') }}<br>{{ t('Bring knowledge, memory, and skills into one filesystem. Build agents that carry their context forward.', '用一个文件系统组织知识、记忆和技能，让 Agent 在每次任务中接着积累。') }}</p>
          <div class="hero-actions">
            <a class="home-button primary" :href="link('getting-started/02-quickstart')">{{ t('Get started', '快速开始') }} <span aria-hidden="true">↗</span></a>
            <a class="home-button secondary" href="#explore">{{ t('Explore the docs', '浏览文档') }} <span aria-hidden="true">↓</span></a>
          </div>
          <p class="hero-footnote">{{ t('Open source. Inspectable context. Your infrastructure.', '开源 · 上下文可读可查 · 自主部署') }}</p>
        </div>
        <div class="context-workspace">
          <div class="workspace-top"><span>OpenViking</span><span class="workspace-caption">{{ t('CONTEXT MODEL', '上下文结构示意') }}</span></div>
          <div class="workspace-body">
            <div class="context-folders" :aria-label="t('Context types', '上下文类型')">
              <span class="tree-root">viking://</span>
              <button v-for="key in ['resources', 'memories', 'agent']" :key="key" :aria-pressed="selected === key" :class="{ selected: selected === key }" @click="selected = key"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2.5 5.5h5l2 2h8v9h-15zM2.5 5.5v-2h5l2 2h8v2" stroke="currentColor" stroke-width="1.2" /></svg>{{ key === 'agent' ? 'agent/' : key }}<span aria-hidden="true">↗</span></button>
              <div class="tree-legend"><span class="status-dot" />{{ t('One filesystem', '同一个文件系统') }}</div>
            </div>
            <div class="context-preview" aria-live="polite">
              <div class="file-path">{{ context.name }}</div>
              <div v-for="(file, index) in context.files" :key="index" class="file-line" :style="{ paddingLeft: `${file.length - file.trimStart().length}ch` }"><span aria-hidden="true">{{ file.endsWith('/') ? '⌄' : '─' }}</span><code>{{ file.trim() }}</code><small v-if="selected === 'resources' && file.includes('abstract')">L0</small><small v-else-if="selected === 'resources' && file.includes('overview')">L1</small><small v-else-if="selected === 'resources' && file.endsWith('.md')">L2</small></div>
              <span class="context-tag">{{ context.tag }}</span>
            </div>
          </div>
          <div class="workspace-bottom"><code>ls <span>·</span> tree <span>·</span> read <span>·</span> find</code><span>{{ t('Familiar by design', '像文件一样使用') }}</span></div>
        </div>
      </div>
      <div class="home-shell hero-index"><span>01 — {{ t('START HERE', '从这里开始') }}</span><span>{{ t('A field guide to agent context', 'Agent 上下文使用手册') }} <span aria-hidden="true">↓</span></span></div>
    </section>

    <div class="home-shell">
      <section class="start-section" :aria-label="t('Choose your path', '选择使用路径')">
        <a v-for="path in paths" :key="path.href" :href="link(path.href)" class="path-card" :class="{ featured: path.primary }">
          <div class="path-top"><span>{{ path.label }}</span><span class="path-icon" aria-hidden="true">{{ path.icon }}</span></div>
          <h2>{{ path.title }}</h2><p>{{ path.description }}</p>
          <span class="path-action">{{ path.action }} <span aria-hidden="true">→</span></span>
        </a>
      </section>
      <div class="install-strip">
        <div><span class="status-dot" /><span>{{ t('Have a server? Start with the CLI.', '已有服务？从 CLI 开始。') }}</span><a :href="link('getting-started/05-cli-setup')">{{ t('Connection guide', '连接指南') }} ↗</a></div>
        <button @click="copyInstall" :aria-label="t('Copy CLI install command', '复制 CLI 安装命令')"><code><span aria-hidden="true">$ </span>{{ install }}</code><span aria-live="polite">{{ copyError ? t('Select to copy', '请选中复制') : copied ? t('Copied', '已复制') : t('Copy', '复制') }}</span></button>
      </div>

      <section class="concept-section" aria-labelledby="concept-title">
        <div class="section-intro"><p class="eyebrow">02 — {{ t('THE MENTAL MODEL', '理解工作方式') }}</p><h2 id="concept-title">{{ t('A filesystem you can reason about.', '上下文，也可以像文件一样清楚。') }}</h2><p>{{ t('Browse the structure. Read the summary. Load the detail when you need it.', '先看目录，再读摘要，需要时才加载全文。') }}</p></div>
        <div class="layer-grid">
          <a :href="link('concepts/03-context-layers')" class="layer-item"><div class="layer-art abstract" aria-hidden="true"><span /><span /></div><div class="layer-heading"><code>L0</code><h3>{{ t('The abstract', '摘要') }}</h3></div><p>{{ t('A short description to decide whether a directory is relevant.', '用一段简述判断目录是否相关。') }}</p><span class="layer-file">.abstract.md <span>↗</span></span></a>
          <a :href="link('concepts/03-context-layers')" class="layer-item"><div class="layer-art overview" aria-hidden="true"><span /><span /><span /><span /></div><div class="layer-heading"><code>L1</code><h3>{{ t('The overview', '概览') }}</h3></div><p>{{ t('Structure and key points to plan what to read next.', '了解结构和要点，决定接下来读什么。') }}</p><span class="layer-file">.overview.md <span>↗</span></span></a>
          <a :href="link('concepts/07-retrieval')" class="layer-item"><div class="layer-art detail" aria-hidden="true"><span /><span /><span /><span /><span /><span /></div><div class="layer-heading"><code>L2</code><h3>{{ t('The source', '原文') }}</h3></div><p>{{ t('The full content, retrieved with its surrounding context.', '按需读取全文，保留内容所在的上下文。') }}</p><span class="layer-file">{{ t('Original content', '原始内容') }} <span>↗</span></span></a>
        </div>
      </section>

      <section class="integration-strip" :aria-label="t('Agent integrations', 'Agent 集成')"><div><p class="eyebrow">03 — {{ t('INTEGRATIONS', 'Agent 集成') }}</p><h2>{{ t('Your agent. With memory.', '你的 Agent，有了记忆。') }}</h2><a :href="link('agent-integrations/01-overview')">{{ t('All integrations', '全部集成') }} ↗</a></div><div class="agent-links"><a v-for="agent in agents" :key="agent[0]" :href="link(`agent-integrations/${agent[1]}`)">{{ agent[0] }}<span aria-hidden="true">↗</span></a></div></section>

      <section class="integration-strip" :aria-label="t('Deployment and operations', '部署与运维')">
        <div><p class="eyebrow">04 — {{ t('DEPLOYMENT', '部署与运维') }}</p><h2>{{ t('Plan your deployment.', '准备部署。') }}</h2><a :href="link('guides/03-deployment')">{{ t('Deployment options', '选择部署方式') }} ↗</a></div>
        <div class="agent-links">
          <a :href="link('guides/01-configuration')">{{ t('Configuration', '基础配置') }}<span aria-hidden="true">↗</span></a>
          <a :href="link('guides/04-authentication')">{{ t('Authentication', '身份认证') }}<span aria-hidden="true">↗</span></a>
          <a :href="link('guides/05-observability')">{{ t('Observability', '可观测性') }}<span aria-hidden="true">↗</span></a>
        </div>
      </section>

      <section id="explore" class="explore-section" aria-labelledby="explore-title">
        <div class="section-intro"><p class="eyebrow">05 — {{ t('DOCUMENTATION ATLAS', '文档地图') }}</p><h2 id="explore-title">{{ t('Find your next step.', '下一步。') }}</h2><p>{{ t('From a first connection to the details of a running system.', '寻找每个细节。') }}</p></div>
        <div class="atlas-layout">
          <aside class="atlas-aside"><label for="doc-filter">{{ t('Find a page', '查找页面') }}</label><div class="atlas-input"><span aria-hidden="true">⌕</span><input id="doc-filter" v-model="filter" type="search" :placeholder="t('Title or filename…', '标题或文件名…')" /></div><p class="atlas-count" role="status">{{ filter ? shown : total }} {{ t('pages', '篇文档') }} <span>/ {{ locale.toUpperCase() }}</span></p><nav :aria-label="t('Documentation sections', '文档章节')"><a v-for="section in atlas" :key="section.id" :href="`#section-${section.id}`" @click="openSection(`section-${section.id}`)">{{ section[locale] }}<span>{{ section.pages.length }} {{ t('pages', '篇') }}</span></a></nav><a class="atlas-machine" :href="withBase('/llms.txt')"><span>↳ llms.txt</span><span>{{ t('For your agent', '给你的 Agent') }} ↗</span></a></aside>
          <div class="atlas-tree"><p v-if="!atlas.length" class="atlas-empty">{{ t('No matching pages. Try “memory”, “API”, or a filename.', '没有匹配页面。试试“记忆”“API”或文件名。') }}</p><details v-for="section in atlas" :id="`section-${section.id}`" :key="`${locale}-${section.id}-${!!filter}`" :open="!!filter || expanded.has(section.id)" @toggle="toggleSection(section.id, $event)" class="atlas-group"><summary><span class="folder-glyph" aria-hidden="true">⌑</span><span><strong>{{ section[locale] }}</strong><small>{{ zh ? section.zhNote : section.enNote }}</small></span><span class="section-count">{{ section.pages.length }} {{ t('pages', '篇') }}</span><span class="expand-glyph" aria-hidden="true">+</span></summary><ul><template v-for="(page, index) in section.pages" :key="page.href"><li v-if="page.group && page.group !== section.pages[index - 1]?.group" class="atlas-subgroup">{{ page.group }}</li><li><a :href="withBase(page.href)"><span class="page-branch" aria-hidden="true">↳</span><span>{{ page.title }}</span><span class="page-arrow" aria-hidden="true">↗</span></a></li></template></ul></details></div>
        </div>
      </section>
      <section class="home-closing"><div><p class="eyebrow">06 — {{ t('BUILT IN THE OPEN', '一起构建') }}</p><h2>{{ t('Keep exploring.', '继续探索。') }}</h2><p>{{ t('Read the source, ask a question, or help shape what comes next.', '读源码、提问题，或参与下一步的构建。') }}</p></div><div><a href="https://github.com/volcengine/OpenViking">GitHub <span>↗</span></a><a :href="link('about/02-changelog')">{{ t('Changelog', '更新日志') }} <span>↗</span></a><a :href="link('about/01-about-us')">{{ t('Community', '加入社区') }} <span>↗</span></a></div></section>
    </div>
  </div>
</template>
