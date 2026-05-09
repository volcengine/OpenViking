/* posts/horizon/index.jsx — short, EN-only post; demonstrates language-fallback */
(function () {
  const Horizon = ({ t }) => (
    <Article>
      <Lead>{t({ en: 'Notes on long horizons, written on a short evening.' })}</Lead>

      <P dropCap>{t({ en: 'The horizon is not a place. It is the line where what you can see ends — and the act of moving toward it changes nothing about its distance. This is true of most things worth working on for a decade.' })}</P>

      <H2>{t({ en: 'Three useful lies' })}</H2>

      <Ol>
        <Li>{t({ en: 'You will know when you are close.' })}</Li>
        <Li>{t({ en: 'Pace is a property of you, not of the road.' })}</Li>
        <Li>{t({ en: 'Other people are walking the same road.' })}</Li>
      </Ol>

      <P>{t({ en: 'Each is wrong in a slightly different shape. The first comforts you in the early years. The second blames you for slowness. The third pretends solidarity where there is only direction. Knowing the shape of each lie is more useful than not believing them.' })}</P>

      <Pull>{t({ en: 'A long horizon is a discipline, not a distance.' })}</Pull>

      <Quote cite="Jenny Odell">{t({ en: 'Things that don\'t fit on a calendar can still be planned.' })}</Quote>

      <Hr ornament />

      <P>{t({ en: 'I am not sure the horizon does anything for you, except be there. Maybe that is enough. The line keeps the ground from looking arbitrary; the walk keeps the day from looking the same as yesterday.' })}</P>
    </Article>
  );

  window.Blog.register({
    id: 'horizon',
    Component: Horizon,
    meta: {
      title: { en: 'On long horizons', zh: '论长远的地平线' },
      description: { en: 'Three useful lies you tell yourself about long, slow projects.', zh: '关于长期、缓慢项目,你对自己讲的三个有用的谎言。' },
      cover: 'assets/covers/horizon.svg',
      publishedAt: '2026-02-14',
      readingTime: 3,
      category: { en: 'Essays', zh: '随笔' },
      tags: ['craft', 'time'],
      languages: ['en'], // intentionally EN-only — shell shows the "not translated" notice when zh is selected
      authors: [{ name: 'Lin Wei', github: 'linwei', avatar: 'assets/avatars/lin.svg', role: { en: 'Engineer', zh: '工程师' } }],
    },
  });
})();
