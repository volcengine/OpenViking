<template>
  <figure class="storage-diagram">
    <svg viewBox="0 0 400 200" role="img" aria-labelledby="storage-diagram-title storage-diagram-desc">
      <title id="storage-diagram-title">Dual-layer storage</title>
      <desc id="storage-diagram-desc">
        VikingFS maps viking:// URIs and hierarchical access onto two stores: the vector index for
        semantic search and AGFS for file content.
      </desc>
      <defs>
        <marker id="storage-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="arrow-head" d="M1 1.5 9 5 1 8.5z" />
        </marker>
      </defs>

      <g class="arrows" marker-end="url(#storage-diagram-arrow)">
        <path d="M200 66v24h-95v26" />
        <path d="M200 66v24h95v26" />
      </g>

      <rect class="box core" x="40" y="8" width="320" height="58" rx="8" />
      <text class="name" x="200" y="30">VikingFS</text>
      <text class="note" x="200" y="51">URI mapping · hierarchical access</text>

      <rect class="box" x="20" y="118" width="170" height="70" rx="8" />
      <text class="name" x="105" y="142">Vector index</text>
      <text class="note" x="105" y="163">semantic search</text>

      <rect class="box" x="210" y="118" width="170" height="70" rx="8" />
      <text class="name" x="295" y="142">AGFS</text>
      <text class="note" x="295" y="163">file content</text>
    </svg>
  </figure>
</template>

<style scoped>
.storage-diagram {
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

.arrows path {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 1.25;
}

.arrow-head {
  fill: var(--vp-c-text-2);
}
</style>
