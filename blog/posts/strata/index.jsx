/* posts/strata/index.jsx */
(function () {
  const Strata = ({ t }) => {
    const T = t;
    return (
      <Article>
        <Lead>{T({
          en: 'A design system is a sediment, not a monument. It accumulates from the work, layer by layer, and the layers are visible in the rock.',
          zh: '设计系统是沉积物,不是纪念碑。它由工作层层积累而成,而那些层在岩石中清晰可见。',
        })}</Lead>

        <P dropCap>{T({
          en: 'Pull any mature system apart and you will find strata. The earliest layer is the loudest — it set the typographic voice, the color of warnings, the corner radius nobody questioned for two years. Above it sits the layer of the first redesign, when somebody dared to soften an edge. Above that, the layer of compromise: a second button color introduced for a single campaign, never removed.',
          zh: '把任何一个成熟的系统拆开,你都会找到一层层地层。最早的一层最响:它定下了排印的语气、警告的颜色、那个没人质疑过的圆角半径。上面是第一次改版的层,有人敢于把一条边缘变柔。再上面,是妥协的层:为一次活动引入的第二种按钮颜色,从未被移除。',
        })}</P>

        <H2>{T({ en: 'Reading the layers', zh: '阅读那些层' })}</H2>
        <P>{T({
          en: 'When I join a system, my first week is archeology. I read the changelog of the tokens file. I diff the current button against the one in the founding RFC. I look for the comment that says "// TODO: align with new spec" and was written four years ago.',
          zh: '当我加入一个系统时,第一周是考古。我阅读 tokens 文件的变更历史。我把现在的按钮与最初 RFC 中的按钮做差异比对。我去寻找那个写于四年前的注释:"// TODO: 与新规范对齐"。',
        })}</P>

        <Cols count={2}>
          <Col>
            <H3>{T({ en: 'Founding layer', zh: '奠基层' })}</H3>
            <P>{T({ en: 'Often a single designer’s taste. Coherent, opinionated, narrow.', zh: '通常是某一位设计师的品味:一致、有主见、范围窄。' })}</P>
          </Col>
          <Col>
            <H3>{T({ en: 'Growth layer', zh: '生长层' })}</H3>
            <P>{T({ en: 'Components multiplied. Variants per component multiplied harder.', zh: '组件增多。每个组件的变体增得更多。' })}</P>
          </Col>
        </Cols>

        <Callout type="note">
          <P>{T({
            en: 'A useful exercise: list every component that exists in your library but appears nowhere in the product. Each one is a fossil. Some deserve preservation; most do not.',
            zh: '一个有用的练习:列出所有存在于组件库但不出现在产品中的组件。每一个都是化石。有些值得保存,大多数不值得。',
          })}</P>
        </Callout>

        <H2>{T({ en: 'When to dig, when to build over', zh: '何时挖掘,何时覆盖' })}</H2>
        <P>{T({
          en: 'Old layers are not always wrong. Often they are right but quietly: they encode constraints — accessibility, screen sizes, brand contracts — that the team has forgotten the reason for but is still bound by. Tearing them out without learning the reason is how you produce a redesign that ships, gets praised internally, and quietly breaks something for a customer six months later.',
          zh: '旧层并非总是错的。它们经常是悄悄地对的:它们编码着团队已经忘记原因、却仍受其约束的边界 —— 无障碍、屏幕尺寸、品牌契约。在不弄清原因的情况下把它们拔掉,正是那种"内部叫好、半年后悄悄伤到某个客户"的改版的来源。',
        })}</P>

        <Pull>{T({
          en: 'A redesign that ships in a week was probably built on top of a year of someone else’s thinking.',
          zh: '一周内上线的改版,很可能建立在他人一年的思考之上。',
        })}</Pull>

        <H2>{T({ en: 'A short field guide', zh: '简短的田野指南' })}</H2>
        <Ul marker="check">
          <Li>{T({ en: 'Date your changes in the file itself, not just in git.', zh: '在文件本身、而不仅仅在 git 中,为你的改动标注日期。' })}</Li>
          <Li>{T({ en: 'When you delete, leave a one-line gravestone explaining why.', zh: '当你删除时,留下一行墓志铭说明缘由。' })}</Li>
          <Li>{T({ en: 'When you fork, name the fork honestly. "v2" is not honest.', zh: '当你 fork 时,诚实地命名。"v2"不诚实。' })}</Li>
          <Li>{T({ en: 'Once a year, walk the layers. Tell the team what you found.', zh: '每年一次,走过那些层,告诉团队你发现了什么。' })}</Li>
        </Ul>

        <Hr ornament />

        <P>{T({
          en: 'Maintained well, a system becomes more legible over time, not less. The strata stay readable. New designers can place themselves in the lineage. The work stops being an act of authorship and becomes an act of stewardship — which is almost always the work.',
          zh: '维护得好,系统会随时间变得更易读,而非更难读。地层保持清晰。新设计师能在血脉中找到自己的位置。工作不再是创作,而是守护 —— 而这,几乎总是工作的本来面目。',
        })}</P>
      </Article>
    );
  };

  window.Blog.register({
    id: 'strata',
    Component: Strata,
    meta: {
      title: { en: 'Strata: reading old design systems', zh: '地层:阅读旧的设计系统' },
      description: {
        en: 'On treating a design system like a sediment — and the kinds of attention that requires.',
        zh: '把设计系统当作沉积物来对待,以及这需要怎样的注意力。',
      },
      cover: 'assets/covers/strata.svg',
      publishedAt: '2026-03-30',
      readingTime: 7,
      category: { en: 'Design', zh: '设计' },
      tags: ['design', 'craft'],
      languages: ['en', 'zh'],
      authors: [{ name: 'Maya Reyes', github: 'mayareyes', avatar: 'assets/avatars/maya.svg', role: { en: 'Design lead', zh: '设计主管' } }],
    },
  });
})();
