/* posts/kitchen-sink/index.jsx
 * The reference post. Demonstrates EVERY hook and primitive a Page may use.
 * Read this file alongside CLAUDE.md before authoring a new post.
 */
(function () {
  const KitchenSink = (props) => {
    // The shell injects these props (also available via useBlog/useLang/useT hooks):
    //   lang        — active language code, narrowed to one this post supports
    //   theme       — active theme id (do NOT branch visuals on this; use primitives)
    //   t           — translator: t({ en, zh }) -> string
    //   formatDate  — locale-aware date formatter
    //   navigate    — programmatic router: navigate('#/post/other-slug')
    const { lang, t, formatDate } = props;

    const T = t; // alias for inline locale literals
    const myDate = formatDate('2026-04-12');

    return (
      <Article>
        <Lead>
          {T({
            en: 'This essay is the spec, not the literature. Every primitive a post can use is rendered below — copy from here when authoring.',
            zh: '本文是规范,不是文学。下方渲染了文章可使用的全部基础组件 —— 撰写时请从此处复制。',
          })}
        </Lead>

        <P dropCap>
          {T({
            en: 'A blog page is a React component registered against window.Blog. It receives the active language and theme as props, and composes from a small library of primitives — Article, H2, P, Figure, Callout, Pre, Quote, Pull, TOC, Cols, Tag, Table. The primitives carry the visual contract; themes restyle them. You write structure and content; the system handles aesthetics.',
            zh: '一篇博客页面是一个注册在 window.Blog 上的 React 组件。它从 props 接收当前语言和主题,并由一小组基础组件 —— Article、H2、P、Figure、Callout、Pre、Quote、Pull、TOC、Cols、Tag、Table —— 组合而成。基础组件承担视觉契约,主题负责重新样式化。你只写结构与内容,系统处理美学。',
          })}
        </P>

        <H2>{T({ en: 'Locale-aware text', zh: '语言感知的文本' })}</H2>
        <P>
          {T({
            en: 'Use the t() function (or its alias T) on every literal. Pass an object keyed by language; the active locale is selected for you. Strings without translations fall back to English.',
            zh: '在每个文本字面量上使用 t() 函数(或其别名 T)。传入以语言为键的对象,系统会自动选择当前语言。未翻译的字符串会回退到英文。',
          })}
        </P>

        <Pre lang="js" filename="posts/example/index.jsx">{`const Example = ({ t }) => (
  <P>
    {t({
      en: 'Hello, world.',
      zh: '你好,世界。',
    })}
  </P>
);`}</Pre>

        <Callout type="tip">
          <P>{T({
            en: 'Prefer T() over conditional rendering on lang. It keeps content side-by-side and translation drift visible in code review.',
            zh: '相比基于 lang 的条件渲染,优先使用 T()。它让内容并排出现,翻译漂移在代码审查中一目了然。',
          })}</P>
        </Callout>

        <H2>{T({ en: 'Headings & hierarchy', zh: '标题与层级' })}</H2>
        <P>{T({ en: 'H2 starts a section. H3 is a sub-section. The TOC auto-collects them.', zh: 'H2 启动一个章节,H3 是子章节,目录自动收集。' })}</P>

        <H3>{T({ en: 'A nested topic', zh: '一个嵌套话题' })}</H3>
        <P>{T({ en: 'Headings can carry an eyebrow for context.', zh: '标题可以带一个 eyebrow 提示上下文。' })}</P>

        <H3 eyebrow={T({ en: 'PATTERN', zh: '模式' })}>
          {T({ en: 'With an eyebrow', zh: '带 eyebrow 的标题' })}
        </H3>
        <P>{T({ en: 'Eyebrows render in monospace and serve as kicker labels.', zh: 'Eyebrow 以等宽字体渲染,作为引导标签。' })}</P>

        <H2>{T({ en: 'Inline formatting', zh: '行内格式' })}</H2>
        <P>
          {T({ en: 'Use ', zh: '使用 ' })}
          <Strong>{T({ en: 'Strong', zh: 'Strong' })}</Strong>
          {T({ en: ', ', zh: '、' })}
          <Em>{T({ en: 'Em', zh: 'Em' })}</Em>
          {T({ en: ', ', zh: '、' })}
          <InlineCode>useBlog()</InlineCode>
          {T({ en: ', ', zh: '、' })}
          <Mark>{T({ en: 'highlights', zh: '高亮' })}</Mark>
          {T({ en: ', and key combos like ', zh: ',以及组合键如 ' })}
          <Kbd>⌘</Kbd>
          {' '}
          <Kbd>K</Kbd>
          {T({ en: '. External links open in a new tab: ', zh: '。外部链接在新标签打开:' })}
          <A href="https://react.dev">react.dev</A>
          {T({ en: '. Internal: ', zh: '。内部链接:' })}
          <A href="#/">{T({ en: 'home', zh: '主页' })}</A>.
        </P>

        <H2>{T({ en: 'Lists', zh: '列表' })}</H2>
        <Cols count={2}>
          <Col>
            <Ul>
              <Li>{T({ en: 'Standard list item', zh: '标准列表项' })}</Li>
              <Li>{T({ en: 'Another one', zh: '又一个' })}</Li>
              <Li>{T({ en: 'Wraps gracefully', zh: '优雅换行' })}</Li>
            </Ul>
          </Col>
          <Col>
            <Ul marker="check">
              <Li>{T({ en: 'Marker variants exist', zh: '标记变体' })}</Li>
              <Li>{T({ en: 'Pass marker="check"', zh: '传入 marker="check"' })}</Li>
            </Ul>
          </Col>
        </Cols>

        <H2>{T({ en: 'Code blocks with syntax', zh: '带语法着色的代码块' })}</H2>
        <Pre lang="js" filename="posts/hello/index.jsx">{`window.Blog.register({
  id: 'hello',
  meta: {
    title: { en: 'Hello', zh: '你好' },
    publishedAt: '2026-05-09',
    languages: ['en', 'zh'],
    authors: [{ name: 'Lin Wei', github: 'linwei' }],
  },
  Component: ({ t }) => (
    <Article>
      <H1>{t({ en: 'Hello', zh: '你好' })}</H1>
      <P>{t({ en: 'World.', zh: '世界。' })}</P>
    </Article>
  ),
});`}</Pre>

        <H2>{T({ en: 'Quotations', zh: '引用' })}</H2>
        <Quote cite="Frank Chimero, The Shape of Design">
          {T({
            en: 'People ignore design that ignores people.',
            zh: '设计忽视人,人也将忽视设计。',
          })}
        </Quote>

        <Pull>
          {T({
            en: 'A pull quote is louder than a block quote and lives in the reading flow.',
            zh: 'Pull quote 比 block quote 更响亮,生活在阅读流之中。',
          })}
        </Pull>

        <H2>{T({ en: 'Figures & images', zh: '图与图片' })}</H2>
        <Figure src="assets/covers/grid.svg"
          caption={T({ en: 'A figure with caption and credit.', zh: '一张带说明与署名的图。' })}
          credit="© Blog Station" />

        <H2>{T({ en: 'Callouts', zh: '提示框' })}</H2>
        <Callout type="note"><P>{T({ en: 'A neutral aside, for context.', zh: '中性的旁注,用于补充上下文。' })}</P></Callout>
        <Callout type="tip"><P>{T({ en: 'A nudge in a useful direction.', zh: '一个有用的方向提示。' })}</P></Callout>
        <Callout type="warn"><P>{T({ en: 'Reader, beware. This bites.', zh: '读者请注意,此处会咬人。' })}</P></Callout>

        <H2>{T({ en: 'Tables', zh: '表格' })}</H2>
        <Table
          headers={[T({ en: 'Hook', zh: 'Hook' }), T({ en: 'Returns', zh: '返回' }), T({ en: 'When', zh: '何时使用' })]}
          rows={[
            [<InlineCode>useLang()</InlineCode>, T({ en: "'en' | 'zh' | …", zh: "'en' | 'zh' | …" }), T({ en: 'Branch on language', zh: '基于语言分支' })],
            [<InlineCode>useT()</InlineCode>, T({ en: 'function', zh: '函数' }), T({ en: 'Inline locale literals', zh: '行内多语种字面量' })],
            [<InlineCode>useTheme()</InlineCode>, T({ en: 'theme id', zh: '主题 id' }), T({ en: 'Rarely; visuals are the theme’s job', zh: '极少使用,视觉由主题负责' })],
            [<InlineCode>useFormatDate()</InlineCode>, T({ en: 'function', zh: '函数' }), T({ en: 'Locale-formatted dates', zh: '本地化日期' })],
          ]} />

        <H2>{T({ en: 'Tags & meta', zh: '标签与元数据' })}</H2>
        <P>
          {T({ en: 'Tag inline:', zh: '行内标签:' })}{' '}
          <Tag>system</Tag> <Tag>i18n</Tag> <Tag>spec</Tag>
        </P>

        <Hr ornament />

        <H2>{T({ en: 'The page contract', zh: '页面契约' })}</H2>
        <P>
          {T({
            en: 'Every post registers itself on window.Blog with three things: a stable id (= URL slug), a meta object, and a Component. The Component receives lang, theme, t, formatDate and navigate as props — and the same values are available via the useBlog hook anywhere inside it.',
            zh: '每篇文章在 window.Blog 上注册三样东西:稳定的 id(即 URL slug)、meta 元数据对象、以及 Component 组件。组件从 props 接收 lang、theme、t、formatDate 和 navigate —— 内部任何位置也可通过 useBlog hook 访问相同的值。',
          })}
        </P>

        <P>
          {T({
            en: 'See CLAUDE.md at the project root for the full meta schema, file layout, and authoring checklist.',
            zh: '完整的 meta schema、文件结构与撰写清单见项目根目录下的 CLAUDE.md。',
          })}
        </P>
      </Article>
    );
  };

  window.Blog.register({
    id: 'kitchen-sink',
    Component: KitchenSink,
    meta: {
      title: { en: 'The kitchen sink', zh: '厨房水槽 —— 全功能演示' },
      description: {
        en: 'Every primitive, hook and pattern a post can use, in one place. Read this when you want to know what is possible.',
        zh: '一篇文章能用到的所有基础组件、hook 与模式,集中演示。想知道可能性时,读这一篇。',
      },
      cover: 'assets/covers/grid.svg',
      publishedAt: '2026-05-09',
      updatedAt: '2026-05-09',
      readingTime: 8,
      category: { en: 'Reference', zh: '参考' },
      tags: ['spec', 'i18n', 'system'],
      languages: ['en', 'zh'],
      authors: [
        { name: 'Lin Wei', github: 'linwei', avatar: 'assets/avatars/lin.svg', role: { en: 'Engineer', zh: '工程师' } },
      ],
    },
  });
})();
