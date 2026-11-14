// 侧边栏的分组和页面只在这里写一遍，中英文共用，两种语言的页面顺序因此保持一致。
//
// Sidebar groups and pages are declared once and shared by both languages, so
// the page order stays identical.

type Lang = 'zh' | 'en';

interface Page {
  /** 相对分类目录的页面名，空字符串是分类概览。Page name under the category; empty is the overview. */
  slug: string;
  zh: string;
  en: string;
}

interface Group {
  zh: string;
  en: string;
  pages: Page[];
}

const guide: Group[] = [
  {
    zh: '指南',
    en: 'Guide',
    pages: [
      { slug: 'getting-started', zh: '快速开始', en: 'Getting started' },
      { slug: 'analytics', zh: '埋点', en: 'Analytics' },
      { slug: 'i18n', zh: '国际化', en: 'Internationalization' },
      { slug: 'changelog', zh: '发布日志', en: 'Release notes' },
    ],
  },
];

const components: Group[] = [
  {
    zh: '业务组件',
    en: 'Business components',
    pages: [
      { slug: '', zh: '概览', en: 'Overview' },
      { slug: 'icons', zh: 'Icons 图标', en: 'Icons' },
    ],
  },
  {
    zh: '文件与目录',
    en: 'Files and directories',
    pages: [
      {
        slug: 'context-picker',
        zh: 'ContextPicker 文件与目录选择',
        en: 'ContextPicker',
      },
      { slug: 'file-tree', zh: 'FileTree 文件树', en: 'FileTree' },
      { slug: 'file-preview', zh: 'FilePreview 文件预览', en: 'FilePreview' },
      {
        slug: 'file-browser-action-bar',
        zh: 'FileBrowserActionBar 文件浏览操作栏',
        en: 'FileBrowserActionBar',
      },
      {
        slug: 'directory-tour',
        zh: 'DirectoryTour 目录引导',
        en: 'DirectoryTour',
      },
    ],
  },
  {
    zh: '对话与命令',
    en: 'Conversation and commands',
    pages: [
      { slug: 'viking-bot', zh: 'VikingBot 对话', en: 'VikingBot' },
      {
        slug: 'command-console',
        zh: 'CommandConsole 命令控制台',
        en: 'CommandConsole',
      },
    ],
  },
  {
    zh: '账号与连接',
    en: 'Accounts and connections',
    pages: [
      {
        slug: 'scope-switcher',
        zh: 'ScopeSwitcher 作用域选择',
        en: 'ScopeSwitcher',
      },
      {
        slug: 'agent-connect',
        zh: 'AgentConnect Agent 接入',
        en: 'AgentConnect',
      },
      { slug: 'auth-forms', zh: '登录与认证表单', en: 'Auth forms' },
    ],
  },
  {
    zh: '数据看板',
    en: 'Dashboards',
    pages: [
      {
        slug: 'wiki-dashboard',
        zh: 'WikiDashboard Wiki 看板',
        en: 'WikiDashboard',
      },
      {
        slug: 'experience-dashboard',
        zh: 'ExperienceDashboard 经验看板',
        en: 'ExperienceDashboard',
      },
    ],
  },
];

const modules: Group[] = [
  {
    zh: '区块',
    en: 'Blocks',
    pages: [{ slug: '', zh: '概览', en: 'Overview' }],
  },
  {
    zh: '核心交互',
    en: 'Core interactions',
    pages: [
      { slug: 'file-browser', zh: 'FileBrowser 文件浏览', en: 'FileBrowser' },
      { slug: 'console', zh: 'Console 交互面板', en: 'Console' },
    ],
  },
  {
    zh: '辅助能力',
    en: 'Supporting capabilities',
    pages: [
      {
        slug: 'scope-cascade',
        zh: 'ScopeCascade 级联选择',
        en: 'ScopeCascade',
      },
    ],
  },
];

// key 是 Rspress 用来选择侧边栏的路由前缀；dir 是分类所在的目录。
const categories = [
  { key: 'guide/', dir: 'guide', groups: guide },
  { key: 'components/', dir: 'components', groups: components },
  { key: 'modules', dir: 'modules', groups: modules },
];

export function createSidebar(lang: Lang) {
  const prefix = lang === 'zh' ? '' : '/en';
  return Object.fromEntries(
    categories.map(({ key, dir, groups }) => [
      `${prefix}/${key}`,
      groups.map((group) => ({
        text: group[lang],
        items: group.pages.map((page) => ({
          text: page[lang],
          link: `${prefix}/${dir}/${page.slug}`,
        })),
      })),
    ]),
  );
}
