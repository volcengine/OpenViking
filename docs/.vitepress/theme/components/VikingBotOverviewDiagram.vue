<template>
  <figure class="bot-diagram">
    <svg viewBox="0 0 400 470" role="img" aria-labelledby="bot-diagram-title bot-diagram-desc">
      <title id="bot-diagram-title">VikingBot system overview</title>
      <desc id="bot-diagram-desc">
        Channels (CLI, Feishu, Slack, Telegram, Discord, Email, HTTP API) feed the channel layer and
        message bus, which drives the AgentLoop: context, model, tools, model. The loop reads
        OpenViking context (resources, memories, experiences, sessions) and runs tools and skills
        (files, shell, web, MCP, cron, subagents); replies and events go back to the channel, and the
        session is synchronized to OpenViking for learning.
      </desc>
      <defs>
        <marker id="bot-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="arrow-head" d="M1 1.5 9 5 1 8.5z" />
        </marker>
      </defs>

      <g class="arrows" marker-end="url(#bot-diagram-arrow)">
        <path d="M200 66v22" />
        <path d="M200 146v22" />
        <path d="M105 246v16" />
        <path d="M295 246v16" />
        <path d="M105 334v24h95v30" />
        <path d="M295 334v24h-95v30" />
      </g>
      <path class="arrows back" d="M320 197h30v-80h-28" marker-end="url(#bot-diagram-arrow)" />
      <text class="edge" x="360" y="157">replies</text>

      <rect class="box" x="20" y="8" width="360" height="58" rx="8" />
      <text class="name" x="200" y="30">Channels</text>
      <text class="note" x="200" y="51">CLI · Feishu · Slack · Telegram · Discord · Email · HTTP</text>

      <rect class="box" x="80" y="88" width="240" height="58" rx="8" />
      <text class="name" x="200" y="110">Channel + MessageBus</text>
      <text class="note" x="200" y="131">routes messages and events</text>

      <rect class="box core" x="80" y="168" width="240" height="58" rx="8" />
      <text class="name" x="200" y="190">AgentLoop</text>
      <text class="note" x="200" y="211">context → model → tools → model</text>

      <rect class="box" x="20" y="262" width="170" height="72" rx="8" />
      <text class="name" x="105" y="284">OpenViking context</text>
      <text class="note" x="105" y="304">resources · memories</text>
      <text class="note" x="105" y="321">experiences · sessions</text>

      <rect class="box" x="210" y="262" width="170" height="72" rx="8" />
      <text class="name" x="295" y="284">Tools / Skills</text>
      <text class="note" x="295" y="304">files · shell · web</text>
      <text class="note" x="295" y="321">MCP · cron · subagents</text>

      <rect class="box" x="70" y="390" width="260" height="58" rx="8" />
      <text class="name" x="200" y="412">Session sync and learning</text>
      <text class="note" x="200" y="433">commit to OpenViking</text>
    </svg>
  </figure>
</template>

<style scoped>
.bot-diagram {
  max-width: 460px;
  margin: 16px auto;
}

svg {
  display: block;
  width: 100%;
  height: auto;
  font-family: inherit;
}

text {
  text-anchor: middle;
  dominant-baseline: middle;
}

.name {
  font-size: 15.5px;
  font-weight: 600;
  fill: var(--vp-c-text-1);
}

.note {
  font-size: 13px;
  fill: var(--vp-c-text-2);
}

.edge {
  font-size: 12.5px;
  fill: var(--vp-c-text-2);
}

.box {
  fill: var(--vp-c-bg-soft);
  stroke: var(--vp-c-divider);
  stroke-width: 1;
}

.box.core {
  fill: var(--vp-c-brand-soft);
  stroke: var(--vp-c-brand-1);
  stroke-width: 1.5;
}

.arrows path,
.arrows.back {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 1.25;
}

.arrows.back {
  stroke-dasharray: 5 4;
}

.arrow-head {
  fill: var(--vp-c-text-2);
}
</style>
