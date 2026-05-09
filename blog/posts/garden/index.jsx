/* posts/garden/index.jsx */
(function () {
  const GardenPost = ({ t }) => {
    const T = t;
    return (
      <Article>
        <Lead>{T({
          en: 'A digital garden is not a blog. It is a space where ideas are planted, watered, and sometimes pulled. This site is, mostly, a blog.',
          zh: '数字花园不是博客。它是种植、浇灌、有时拔除想法的空间。这个站点,大部分时候,是一个博客。',
        })}</Lead>

        <P dropCap>{T({
          en: 'I have read a lot of essays about digital gardens by people who do not, themselves, garden. The metaphor is generous to the point of misleading. A real garden is mostly weeds and waiting; a digital garden, in practice, is a list of half-written notes nobody re-reads.',
          zh: '我读过许多关于数字花园的文章,作者本人却并不种花。这个比喻慷慨到近乎误导。真实的花园主要是杂草与等待;实践中的数字花园,则常是一堆没人重读的半成品笔记。',
        })}</P>

        <H2>{T({ en: 'What a garden actually does', zh: '花园实际在做什么' })}</H2>
        <P>{T({
          en: 'A garden enforces a clock. The tomato does not care that you are busy. If the rain comes you adjust the trellis or you do not, and the trellis remembers either way. Most of my notebooks would benefit from a trellis.',
          zh: '花园强加一个时钟。番茄不在乎你忙不忙。雨来了,你调整支架,或不调整,而支架无论如何都会记得。我的大多数笔记本,都需要一个支架。',
        })}</P>

        <Quote cite={T({ en: 'Maggie Appleton', zh: 'Maggie Appleton' })}>
          {T({
            en: 'Tend to your notes the way you tend to plants — slowly, repeatedly, with care.',
            zh: '像照料植物那样照料你的笔记 —— 缓慢、反复、用心。',
          })}
        </Quote>

        <H2>{T({ en: 'A small system', zh: '一个小系统' })}</H2>
        <P>{T({ en: 'I keep three folders. Seedlings are notes I am not sure are notes yet. Tended is anything I have re-opened on purpose at least twice. Harvested is what becomes essays — like this one.', zh: '我保留三个文件夹。Seedlings 是我还不确定算不算笔记的笔记;Tended 是我至少有意重新打开过两次的内容;Harvested 是变成文章的东西 —— 比如这一篇。' })}</P>

        <Cols count={2}>
          <Col>
            <H3>{T({ en: 'Seedlings', zh: '幼苗' })}</H3>
            <P>{T({ en: 'No structure, no audience, low stakes.', zh: '没有结构、没有读者、低风险。' })}</P>
          </Col>
          <Col>
            <H3>{T({ en: 'Tended', zh: '已护理' })}</H3>
            <P>{T({ en: 'A title, a paragraph, a question.', zh: '一个标题、一段话、一个问题。' })}</P>
          </Col>
        </Cols>

        <Callout type="tip">
          <P>{T({ en: 'The folders are less important than the act of moving things between them. The motion is the gardening.', zh: '文件夹本身没有多重要,重要的是把东西在它们之间搬来搬去的动作。那动作,才是园艺。' })}</P>
        </Callout>

        <Hr ornament />

        <P>{T({ en: 'A garden does not need to be ambitious. It needs to be tended. The same is true of a body of work.', zh: '花园不必雄心勃勃,它需要被照料。一份作品集亦然。' })}</P>
      </Article>
    );
  };

  window.Blog.register({
    id: 'garden',
    Component: GardenPost,
    meta: {
      title: { en: 'Tending a small garden', zh: '照料一个小花园' },
      description: { en: 'Notes-as-plants, in practice rather than metaphor.', zh: '笔记即植物 —— 不是比喻,是实践。' },
      cover: 'assets/covers/garden.svg',
      publishedAt: '2025-12-12',
      readingTime: 5,
      category: { en: 'Practice', zh: '实践' },
      tags: ['craft', 'time'],
      languages: ['en', 'zh'],
      authors: [{ name: 'Maya Reyes', github: 'mayareyes', avatar: 'assets/avatars/maya.svg', role: { en: 'Design lead', zh: '设计主管' } }],
    },
  });
})();
