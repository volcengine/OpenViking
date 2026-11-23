<script setup>
import { computed, useId } from 'vue'

// Sequence diagram for the at-rest encryption write and read paths.
// Lanes are fixed; `mode` picks the message list.
const props = defineProps({
  mode: { type: String, default: 'write' }
})

const W = 600
const LANES = [
  { key: 'client', x: 64, w: 108, name: 'Client' },
  { key: 'wrapper', x: 238, w: 164, name: 'Encryption wrapper', sub: 'RAGFS' },
  { key: 'key', x: 404, w: 132, name: 'Account key', sub: 'from root key' },
  { key: 'backend', x: 534, w: 108, name: 'Backend', sub: 'local / S3' }
]
const laneX = Object.fromEntries(LANES.map((l) => [l.key, l.x]))

const FLOWS = {
  write: {
    title: 'Encrypted write path',
    desc:
      'The client writes plaintext. The RAGFS encryption wrapper derives the account key, generates a random file key and nonces, encrypts the content with AES-256-GCM, wraps the file key with the account key, and writes the OVE1 envelope to the backend.',
    items: [
      { type: 'msg', from: 'client', to: 'wrapper', label: 'write(uri, plaintext)', y: 86 },
      { type: 'msg', from: 'wrapper', to: 'key', label: 'derive (HKDF, cached)', y: 120 },
      { type: 'msg', from: 'key', to: 'wrapper', label: 'account key', y: 150, dashed: true },
      {
        type: 'note',
        lane: 'wrapper',
        y: 172,
        lines: [
          '1. random File Key + nonces',
          '2. AES-256-GCM encrypt content',
          '3. wrap File Key with account key',
          '4. build OVE1 envelope'
        ]
      },
      { type: 'msg', from: 'wrapper', to: 'backend', label: 'write envelope (ciphertext)', y: 290 },
      { type: 'msg', from: 'backend', to: 'wrapper', label: 'stored', y: 320, dashed: true },
      { type: 'msg', from: 'wrapper', to: 'client', label: 'ok', y: 350, dashed: true }
    ],
    height: 372
  },
  read: {
    title: 'Encrypted read path',
    desc:
      'The client reads a URI. The wrapper fetches the stored bytes; files without the OVE1 magic are returned as plaintext. Otherwise it derives the account key, unwraps the file key, authenticates and decrypts the content, and returns plaintext or an error.',
    items: [
      { type: 'msg', from: 'client', to: 'wrapper', label: 'read(uri)', y: 86 },
      { type: 'msg', from: 'wrapper', to: 'backend', label: 'read bytes', y: 116 },
      { type: 'msg', from: 'backend', to: 'wrapper', label: 'stored bytes', y: 146, dashed: true },
      { type: 'note', lane: 'wrapper', y: 166, lines: ['no OVE1 magic → return as plaintext'] },
      { type: 'msg', from: 'wrapper', to: 'key', label: 'derive (HKDF, cached)', y: 226 },
      { type: 'msg', from: 'key', to: 'wrapper', label: 'account key', y: 256, dashed: true },
      {
        type: 'note',
        lane: 'wrapper',
        y: 278,
        lines: [
          '1. unwrap File Key with account key',
          '2. authenticate + decrypt (AES-GCM)',
          'wrong key or bad tag → error'
        ]
      },
      { type: 'msg', from: 'wrapper', to: 'client', label: 'plaintext', y: 378, dashed: true }
    ],
    height: 400
  }
}

const id = useId()
const arrowId = `enc-arrow-${id}`
const flow = computed(() => FLOWS[props.mode] ?? FLOWS.write)

const NOTE_W = 250
const NOTE_LINE = 18
function noteHeight(n) {
  return n.lines.length * NOTE_LINE + 12
}
function msgPath(m) {
  const x1 = laneX[m.from]
  const x2 = laneX[m.to]
  const dir = x2 > x1 ? -1 : 1
  return `M${x1} ${m.y}H${x2 + dir * 2}`
}
</script>

<template>
  <figure class="enc-diagram">
    <svg :viewBox="`0 0 ${W} ${flow.height}`" role="img" :aria-labelledby="`${id}-t ${id}-d`">
      <title :id="`${id}-t`">{{ flow.title }}</title>
      <desc :id="`${id}-d`">{{ flow.desc }}</desc>
      <defs>
        <marker :id="arrowId" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="arrow-head" d="M1 1.5 9 5 1 8.5z" />
        </marker>
      </defs>

      <g v-for="lane in LANES" :key="lane.key">
        <line class="lifeline" :x1="lane.x" :x2="lane.x" y1="56" :y2="flow.height - 8" />
        <rect class="box" :x="lane.x - lane.w / 2" y="8" :width="lane.w" height="48" rx="8" />
        <text class="name" :x="lane.x" :y="lane.sub ? 25 : 32">{{ lane.name }}</text>
        <text v-if="lane.sub" class="sub" :x="lane.x" y="43">{{ lane.sub }}</text>
      </g>

      <template v-for="(item, i) in flow.items" :key="i">
        <g v-if="item.type === 'msg'">
          <path class="msg" :class="{ dashed: item.dashed }" :d="msgPath(item)" :marker-end="`url(#${arrowId})`" />
          <text class="label" :x="(laneX[item.from] + laneX[item.to]) / 2" :y="item.y - 8">{{ item.label }}</text>
        </g>
        <g v-else>
          <rect
            class="note"
            :x="laneX[item.lane] - NOTE_W / 2"
            :y="item.y"
            :width="NOTE_W"
            :height="noteHeight(item)"
            rx="6"
          />
          <text
            v-for="(line, j) in item.lines"
            :key="j"
            class="note-text"
            :x="laneX[item.lane]"
            :y="item.y + 6 + j * NOTE_LINE + NOTE_LINE / 2"
          >{{ line }}</text>
        </g>
      </template>
    </svg>
  </figure>
</template>

<style scoped>
.enc-diagram {
  max-width: 640px;
  margin: 16px auto;
  overflow-x: auto;
}

svg {
  display: block;
  width: 100%;
  min-width: 540px;
  height: auto;
  font-family: inherit;
}

text {
  text-anchor: middle;
  dominant-baseline: middle;
}

.name {
  font-size: 14px;
  font-weight: 600;
  fill: var(--vp-c-text-1);
}

.sub {
  font-size: 12px;
  fill: var(--vp-c-text-2);
}

.label {
  font-size: 12.5px;
  fill: var(--vp-c-text-1);
}

.note-text {
  font-size: 12.5px;
  fill: var(--vp-c-text-1);
}

.box {
  fill: var(--vp-c-bg-soft);
  stroke: var(--vp-c-divider);
  stroke-width: 1;
}

.note {
  fill: var(--vp-c-brand-soft);
  stroke: var(--vp-c-brand-1);
  stroke-width: 1;
}

.lifeline {
  stroke: var(--vp-c-divider);
  stroke-width: 1;
  stroke-dasharray: 4 4;
}

.msg {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 1.25;
}

.msg.dashed {
  stroke-dasharray: 5 4;
}

.arrow-head {
  fill: var(--vp-c-text-2);
}
</style>
