import { fileURLToPath } from 'node:url';
import { defineConfig } from '@rspress/core';
import { pluginPreview } from '@rspress/plugin-preview';
import { pluginWorkspaceDev } from 'rsbuild-plugin-workspace-dev';

export default defineConfig({
  root: fileURLToPath(new URL('./docs', import.meta.url)),
  base: '/ui/',
  // 默认语言是中文：开发时看到的就是中文页面，地址不带语言前缀；英文移到
  // `/ui/en/` 下。
  //
  // The default language is Chinese, so development shows the Chinese pages
  // without a language prefix; English moves to `/ui/en/`.
  lang: 'zh',
  title: 'OpenViking UI',
  description: 'React UI modules for OpenViking applications.',
  // Brand assets mirror docs/images/{nav-logo-light,nav-logo-dark,favicon}.svg.
  icon: '/favicon.svg',
  logo: { light: '/logo.svg', dark: '/logo-dark.svg' },
  logoText: 'OpenViking UI',
  globalStyles: fileURLToPath(new URL('./styles.css', import.meta.url)),
  plugins: [pluginPreview()],
  builderConfig: {
    plugins: [pluginWorkspaceDev()],
  },
  themeConfig: {
    socialLinks: [
      {
        icon: 'github',
        mode: 'link',
        content: 'https://github.com/volcengine/OpenViking',
      },
    ],
    locales: [
      {
        lang: 'zh',
        label: '简体中文',
        title: 'OpenViking UI',
        description: '面向 OpenViking 应用的 React UI 模块。',
        nav: [
          { text: '指南', link: '/guide/getting-started' },
          { text: '组件', link: '/components/' },
          { text: '业务模块', link: '/modules/' },
          { text: 'OpenViking 文档', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: {
          '/guide/': [
            {
              text: '指南',
              items: [{ text: '快速开始', link: '/guide/getting-started' }],
            },
          ],
          '/components/': [
            {
              text: '组件',
              items: [
                { text: '概览', link: '/components/' },
                { text: '图标', link: '/components/icons' },
                {
                  text: 'FileBrowserEntries 入口条',
                  link: '/components/file-browser-entries',
                },
                { text: 'FileTree 文件树', link: '/components/file-tree' },
                {
                  text: 'FilePreview 文件预览',
                  link: '/components/file-preview',
                },
                { text: 'Tui 命令控制台', link: '/components/tui' },
                {
                  text: 'ScopeSwitcher 作用域选择',
                  link: '/components/scope-switcher',
                },
                {
                  text: 'AgentConnect Agent 接入',
                  link: '/components/agent-connect',
                },
                {
                  text: 'VikingBot 对话',
                  link: '/components/viking-bot',
                },
              ],
            },
          ],
          '/modules': [
            {
              text: '业务模块',
              items: [{ text: '概览', link: '/modules/' }],
            },
            {
              text: '核心交互',
              items: [
                {
                  text: 'FileBrowser 文件浏览',
                  link: '/modules/file-browser',
                },
                { text: 'Console 交互面板', link: '/modules/console' },
              ],
            },
            {
              text: '辅助能力',
              items: [
                {
                  text: 'ScopeCascade 级联选择',
                  link: '/modules/scope-cascade',
                },
              ],
            },
          ],
        },
      },
      {
        lang: 'en',
        label: 'English',
        title: 'OpenViking UI',
        description: 'React UI modules for OpenViking applications.',
        nav: [
          { text: 'Guide', link: '/en/guide/getting-started' },
          { text: 'Components', link: '/en/components/' },
          { text: 'Modules', link: '/en/modules/' },
          { text: 'OpenViking docs', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: {
          '/en/guide/': [
            {
              text: 'Guide',
              items: [
                { text: 'Getting started', link: '/en/guide/getting-started' },
              ],
            },
          ],
          '/en/components/': [
            {
              text: 'Components',
              items: [
                { text: 'Overview', link: '/en/components/' },
                { text: 'Icons', link: '/en/components/icons' },
                {
                  text: 'FileBrowserEntries',
                  link: '/en/components/file-browser-entries',
                },
                { text: 'FileTree', link: '/en/components/file-tree' },
                { text: 'FilePreview', link: '/en/components/file-preview' },
                { text: 'Tui', link: '/en/components/tui' },
                {
                  text: 'ScopeSwitcher',
                  link: '/en/components/scope-switcher',
                },
                { text: 'AgentConnect', link: '/en/components/agent-connect' },
                { text: 'VikingBot', link: '/en/components/viking-bot' },
              ],
            },
          ],
          '/en/modules': [
            {
              text: 'Modules',
              items: [{ text: 'Overview', link: '/en/modules/' }],
            },
            {
              text: 'Core interactions',
              items: [
                { text: 'FileBrowser', link: '/en/modules/file-browser' },
                { text: 'Console', link: '/en/modules/console' },
              ],
            },
            {
              text: 'Supporting capabilities',
              items: [
                { text: 'ScopeCascade', link: '/en/modules/scope-cascade' },
              ],
            },
          ],
        },
      },
    ],
    footer: {
      message: 'OpenViking UI',
    },
  },
});
