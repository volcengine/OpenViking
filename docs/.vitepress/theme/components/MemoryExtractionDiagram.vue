<script setup>
import FlowDiagram from './FlowDiagram.vue'

const steps = [
  { name: 'Archived messages', notes: ['+ enabled memory schemas'] },
  { name: 'ExtractLoop', notes: ['reads relevant existing memories,', 'produces update operations'] },
  { name: 'MemoryUpdater', notes: ['validates and applies', 'additions, edits, deletions'] },
  { name: 'Persist', notes: ['memory files · indexes · memory_diff.json'] }
]
</script>

<template>
  <FlowDiagram
    title="Memory extraction"
    desc="Archived messages and the enabled memory schemas feed ExtractLoop, which produces update operations; MemoryUpdater validates and applies them; memories, indexes and memory_diff.json are then persisted."
    :steps="steps"
    :core="2"
  />
</template>
