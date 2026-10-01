<script setup>
import { computed, useId } from 'vue'

// Vertical flow: one box per step, arrows between them. `notes` lines are
// wrapped by the caller so widths stay predictable at phone size; `edge`
// labels the arrow to the next step.
const props = defineProps({
  title: { type: String, required: true },
  desc: { type: String, required: true },
  steps: { type: Array, required: true },
  // index of the step drawn as the core node (brand border); -1 for none
  core: { type: Number, default: -1 }
})

const W = 400
const BOX_X = 20
const BOX_W = 360
const PAD = 8
const GAP = 30
const NAME_H = 22
const NOTE_H = 18

const id = useId()
const arrowId = `flow-arrow-${id}`

const layout = computed(() => {
  let y = PAD
  const boxes = props.steps.map((step, i) => {
    const notes = step.notes ?? []
    const h = 14 + NAME_H + notes.length * NOTE_H
    const gap = step.edge ? GAP + 10 : GAP
    const box = { ...step, notes, i, y, h, gap }
    y += h + gap
    return box
  })
  const last = boxes[boxes.length - 1]
  return { boxes, height: last.y + last.h + PAD }
})
</script>

<template>
  <figure class="flow-diagram">
    <svg :viewBox="`0 0 ${W} ${layout.height}`" role="img" :aria-labelledby="`${id}-t ${id}-d`">
      <title :id="`${id}-t`">{{ title }}</title>
      <desc :id="`${id}-d`">{{ desc }}</desc>
      <defs>
        <marker :id="arrowId" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="arrow-head" d="M1 1.5 9 5 1 8.5z" />
        </marker>
      </defs>

      <g v-for="box in layout.boxes" :key="box.i">
        <path
          v-if="box.i < layout.boxes.length - 1"
          class="arrow"
          :d="`M${W / 2} ${box.y + box.h}v${box.gap - 2}`"
          :marker-end="`url(#${arrowId})`"
        />
        <text
          v-if="box.edge && box.i < layout.boxes.length - 1"
          class="edge"
          :x="W / 2 + 10"
          :y="box.y + box.h + box.gap / 2"
        >{{ box.edge }}</text>
        <rect class="box" :class="{ core: box.i === core }" :x="BOX_X" :y="box.y" :width="BOX_W" :height="box.h" rx="8" />
        <text class="name" :x="W / 2" :y="box.y + 7 + NAME_H / 2">{{ box.name }}</text>
        <text
          v-for="(line, j) in box.notes"
          :key="j"
          class="note"
          :x="W / 2"
          :y="box.y + 7 + NAME_H + j * NOTE_H + NOTE_H / 2"
        >{{ line }}</text>
      </g>
    </svg>
  </figure>
</template>

<style scoped>
.flow-diagram {
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

.edge {
  font-size: 13px;
  text-anchor: start;
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

.arrow {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 1.25;
}

.arrow-head {
  fill: var(--vp-c-text-2);
}
</style>
