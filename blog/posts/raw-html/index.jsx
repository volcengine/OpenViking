/* posts/raw-html/index.jsx — short brutalist-flavoured piece, EN/ZH */
(function () {
  const RawHtml = ({ t }) => {
    const T = t;
    return (
      <Article>
        <Lead>{T({
          en: 'A short defense of writing the page yourself, in the year of our lord twenty twenty-six.',
          zh: '在 2026 年,为亲手写页面这件事,做一个简短的辩护。',
        })}</Lead>

        <P>{T({
          en: 'The default frontend stack is now four megabytes of JavaScript that renders six paragraphs of text. The framework changed five times while you were reading the docs. The tooling has its own tooling. Somewhere in there is a person trying to publish an essay.',
          zh: '今天默认的前端栈,是四兆字节的 JavaScript 渲染六段文字。在你读完文档前,框架已经换了五次。工具有它自己的工具。在那一切之中,有一个人正想发表一篇文章。',
        })}</P>

        <H2>{T({ en: 'A constraint, not a creed', zh: '一个约束,而非教义' })}</H2>
        <P>{T({
          en: 'I am not anti-framework. I am pro-budget. If your essay needs a dependency graph deeper than your prose, the dependency graph is the essay you actually wrote.',
          zh: '我不反对框架,我支持预算。如果你的文章所需的依赖图比文字本身还深,那么那张依赖图,才是你真正写下的文章。',
        })}</P>

        <Pre lang="js" filename="post.html">{`<article>
  <h1>{title}</h1>
  <p>{body}</p>
</article>`}</Pre>

        <Callout type="warn">
          <P>{T({
            en: 'This essay was written inside a React app. The argument still applies; I just made the trade-off the other way.',
            zh: '这篇文章是在一个 React 应用里写的。论点依然成立;只不过,我这一次站在了另一边。',
          })}</P>
        </Callout>

        <Pull>{T({
          en: 'Every dependency is a small loan against your future attention.',
          zh: '每一个依赖,都是向你未来的注意力借的一笔小钱。',
        })}</Pull>

        <Hr ornament />

        <P>{T({
          en: 'The web has a markup language. It is good. Use less of everything else.',
          zh: '网络有一门标记语言,它很好。其余的一切,都少用一点。',
        })}</P>
      </Article>
    );
  };

  window.Blog.register({
    id: 'raw-html',
    Component: RawHtml,
    meta: {
      title: { en: 'In defense of raw HTML', zh: '为原始 HTML 辩护' },
      description: {
        en: 'A budget-minded argument for writing the page yourself.',
        zh: '一份关于亲手写页面的、有预算意识的辩护。',
      },
      cover: 'assets/covers/brutalist.svg',
      publishedAt: '2026-01-08',
      readingTime: 4,
      category: { en: 'Engineering', zh: '工程' },
      tags: ['web', 'craft'],
      languages: ['en', 'zh'],
      authors: [{ name: 'Sora Okafor', github: 'soraok', avatar: 'assets/avatars/sora.svg', role: { en: 'Frontend', zh: '前端' } }],
    },
  });
})();
