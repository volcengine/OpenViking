/* posts/quiet-signals/index.jsx — a real essay, EN/ZH */
(function () {
  const QuietSignals = ({ t }) => {
    const T = t;
    return (
      <Article>
        <Lead>{T({
          en: 'Most of what makes good engineering work is invisible: a thousand quiet signals saying "this is fine" until one of them stops.',
          zh: '让工程工作行得通的,大多是看不见的东西 —— 一千个安静的信号在说"一切正常",直到其中一个停了。',
        })}</Lead>

        <P dropCap>{T({
          en: 'I keep a small notebook of moments when a system surprised me. Not the dramatic outages — those are rare and well-documented. The smaller surprises: a metric that drifted half a percent over six weeks, a queue that grew slowly until it didn\'t, a build time that doubled because of a transitive dependency I\'d never opened.',
          zh: '我有一本小本子,记录系统给我带来惊讶的瞬间。不是那些戏剧性的故障 —— 它们罕见且文档齐全。我记的是更细小的惊讶:一个指标在六周内漂移了半个百分点;一个队列缓慢增长直到不再增长;某次构建时间翻倍,因为一个我从未打开过的传递依赖。',
        })}</P>

        <P>{T({
          en: 'These signals are quiet because the systems that produce them are working. They are doing what we asked. The discipline of paying attention to them — before they shout — is most of what we mean by "operational maturity."',
          zh: '这些信号之所以安静,是因为产生它们的系统在正常工作 —— 在做我们要求它做的事。对它们的留意 —— 在它们尖叫之前 —— 大致就是我们所说的"运维成熟度"。',
        })}</P>

        <Pull>{T({
          en: 'Most of operations is reading the texture of "fine".',
          zh: '运维的大部分,是阅读"还好"的质地。',
        })}</Pull>

        <H2>{T({ en: 'Three flavors of quiet', zh: '三种安静' })}</H2>
        <P>{T({
          en: 'Drift is the slowest. A number you stopped checking is now meaningfully different from where it was. Latency is the most personal — the system feels heavier, but no graph confirms it. Cost is the most political; you discover it when someone else discovers it.',
          zh: '漂移最慢:一个你不再查看的数字,如今已与原来明显不同。延迟最个人化:系统感觉变重了,但没有图表能证实。成本最政治:你通常是在别人发现后才发现的。',
        })}</P>

        <Callout type="tip">
          <P>{T({
            en: 'For each metric you alert on, keep a second one whose only job is to look pretty. Beauty here means stable shape — a flat line, a clean diurnal curve. When the shape changes, you notice before the alert fires.',
            zh: '对每个告警指标,再保留一个仅用于"好看"的伴随指标。这里的好看意味着稳定的形状 —— 一条平线,或一个干净的昼夜曲线。当形状改变时,你会在告警触发前先注意到。',
          })}</P>
        </Callout>

        <H2>{T({ en: 'How I read a dashboard', zh: '我如何读一张仪表盘' })}</H2>
        <Ol>
          <Li>{T({
            en: 'First pass: scan for shape, not value. Are the curves the curves I expect?',
            zh: '第一遍:扫看形状,不看数值。曲线是我熟悉的曲线吗?',
          })}</Li>
          <Li>{T({
            en: 'Second pass: line up adjacent panels in time. Is anomaly A a child of anomaly B, or its sibling?',
            zh: '第二遍:在时间轴上对齐相邻面板。异常 A 是异常 B 的子,还是兄弟?',
          })}</Li>
          <Li>{T({
            en: 'Third pass: compare to the same hour last week, not the last hour. Most production data has a weekly heartbeat.',
            zh: '第三遍:与上周同一小时比较,而非上一小时。大多数生产数据有一个每周的心跳。',
          })}</Li>
        </Ol>

        <H2>{T({ en: 'A small ritual', zh: '一个小仪式' })}</H2>
        <P>{T({
          en: 'Every Monday, before the first meeting, I open the four dashboards I trust most and look at them for ninety seconds. I don\'t take notes. I just look. Eight times out of ten the only thing I notice is that nothing has changed. The other two times I have ten days of warning instead of ten minutes.',
          zh: '每周一,在第一个会议之前,我打开最信任的四张仪表盘,看上九十秒。我不记笔记,只是看。十次里有八次,我注意到的唯一一件事是什么都没变。另外两次,我得到的是十天的预警,而非十分钟的预警。',
        })}</P>

        <Quote cite={T({ en: 'Anonymous, in an internal review', zh: '佚名,内部评审中' })}>
          {T({
            en: 'The best engineers I know spend a surprising amount of time staring at things that are working.',
            zh: '我认识的最好的工程师,花在凝视那些"正在正常工作"的东西上的时间多得惊人。',
          })}
        </Quote>

        <Hr ornament />

        <P>{T({
          en: 'The hardest part of this practice is that it is mostly invisible to others. You are paid to react to outages; nobody schedules a postmortem for the ones that didn\'t happen. So write it down. Keep your own notebook of quiet signals. Over a year you will have a map of how your system actually behaves — which is a different and better artifact than the one your dashboards show.',
          zh: '这个实践最难的地方是它对他人几乎不可见。你拿薪水是为了应对故障;没有人会为"没发生的故障"安排事后复盘。所以,把它写下来。保留你自己的安静信号本。一年下来,你会拥有一张系统真实行为的地图 —— 它是与仪表盘所展示的不同且更好的工件。',
        })}</P>
      </Article>
    );
  };

  window.Blog.register({
    id: 'quiet-signals',
    Component: QuietSignals,
    meta: {
      title: { en: 'Quiet signals in noisy rooms', zh: '吵闹房间里的安静信号' },
      description: {
        en: 'On the discipline of paying attention to systems that are still working.',
        zh: '关于留意"仍在正常运转"的系统的修养。',
      },
      cover: 'assets/covers/signal.svg',
      publishedAt: '2026-04-22',
      readingTime: 6,
      category: { en: 'Engineering', zh: '工程' },
      tags: ['operations', 'craft'],
      languages: ['en', 'zh'],
      authors: [{ name: 'Kai Tanaka', github: 'kaitnk', avatar: 'assets/avatars/kai.svg', role: { en: 'SRE', zh: '可靠性工程师' } }],
    },
  });
})();
