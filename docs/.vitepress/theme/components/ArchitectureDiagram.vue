<script setup>
// Centers of the three pipeline columns inside the dashed group.
const columns = [60, 185.5, 325.5]
</script>

<template>
  <figure class="arch-diagram">
    <svg viewBox="0 0 400 586" role="img" aria-labelledby="arch-diagram-title arch-diagram-desc">
      <title id="arch-diagram-title">OpenViking system overview</title>
      <desc id="arch-diagram-desc">
        CLI, SDK and HTTP clients call the HTTP Server (REST API and the /mcp endpoint), which calls the
        Service Layer. The Service Layer drives three pipelines: Retrieval; Sessions with memory
        extraction; and Resource and Skill import with parse and semantic queues. All three read and
        write VikingFS, which stores content in AGFS and indexes it in the vector index.
      </desc>
      <defs>
        <marker id="arch-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="arrow-head" d="M1 1.5 9 5 1 8.5z" />
        </marker>
      </defs>

      <g class="arrows" marker-end="url(#arch-diagram-arrow)">
        <path d="M200 52v24" />
        <path d="M200 136v24" />
        <path v-for="x in columns" :key="`in-${x}`" :d="`M${x} 220v34`" />
        <path v-for="x in columns" :key="`out-${x}`" :d="`M${x} 394v34`" />
        <path d="M115 488v30" />
        <path d="M285 488v30" />
      </g>

      <rect class="group" x="4" y="238" width="392" height="170" rx="12" />

      <rect class="box" x="70" y="8" width="260" height="44" rx="8" />
      <text class="name" x="200" y="30">CLI · SDK · HTTP client</text>

      <rect class="box" x="70" y="78" width="260" height="58" rx="8" />
      <text class="name" x="200" y="100">HTTP Server</text>
      <text class="note" x="200" y="121">REST API · /mcp</text>

      <rect class="box" x="4" y="162" width="392" height="58" rx="8" />
      <text class="name" x="200" y="184">Service Layer</text>
      <text class="note" x="200" y="205">FS · Search · Session · Resource · Pack · Debug</text>

      <rect class="box" x="10" y="256" width="100" height="138" rx="8" />
      <text class="name" x="60" y="316">Retrieval</text>
      <text class="note" x="60" y="337">search · find</text>

      <rect class="box" x="121" y="256" width="129" height="58" rx="8" />
      <text class="name" x="185.5" y="285">Sessions</text>
      <path class="step" d="M185.5 314v22" marker-end="url(#arch-diagram-arrow)" />
      <rect class="box sub" x="121" y="336" width="129" height="58" rx="8" />
      <text class="step-name" x="185.5" y="360">Memory</text>
      <text class="step-name" x="185.5" y="378">extraction</text>

      <rect class="box" x="261" y="256" width="129" height="58" rx="8" />
      <text class="name" x="325.5" y="280">Resource /</text>
      <text class="name" x="325.5" y="299">Skill import</text>
      <path class="step" d="M325.5 314v22" marker-end="url(#arch-diagram-arrow)" />
      <rect class="box sub" x="261" y="336" width="129" height="58" rx="8" />
      <text class="step-name" x="325.5" y="360">Parse /</text>
      <text class="step-name" x="325.5" y="378">Semantic queues</text>

      <rect class="box core" x="40" y="430" width="320" height="58" rx="8" />
      <text class="name" x="200" y="452">VikingFS</text>
      <text class="note" x="200" y="473">virtual filesystem</text>

      <rect class="box" x="40" y="520" width="150" height="58" rx="8" />
      <text class="name" x="115" y="542">AGFS</text>
      <text class="note" x="115" y="563">source files</text>

      <rect class="box" x="210" y="520" width="150" height="58" rx="8" />
      <text class="name" x="285" y="542">Vector index</text>
      <text class="note" x="285" y="563">vectors · metadata</text>
    </svg>
  </figure>
</template>

<style scoped>
.arch-diagram {
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
  font-size: 13.5px;
  fill: var(--vp-c-text-2);
}

.step-name {
  font-size: 13.5px;
  font-weight: 500;
  fill: var(--vp-c-text-1);
}

.box {
  fill: var(--vp-c-bg-soft);
  stroke: var(--vp-c-divider);
  stroke-width: 1;
}

.box.sub {
  fill: var(--vp-c-bg);
}

.box.core {
  fill: var(--vp-c-brand-soft);
  stroke: var(--vp-c-brand-1);
  stroke-width: 1.5;
}

.group {
  fill: none;
  stroke: var(--vp-c-divider);
  stroke-dasharray: 5 4;
}

.arrows path,
.step {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 1.25;
}

.arrow-head {
  fill: var(--vp-c-text-2);
}
</style>
