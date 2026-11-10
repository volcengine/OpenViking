<script setup>
import FlowDiagram from './FlowDiagram.vue'

const steps = [
  { name: 'Input', notes: ['file, directory, URL, upload'] },
  { name: 'Parser', notes: ['parse artifacts in a temp location'] },
  { name: 'TreeBuilder', notes: ['resolve the target URI'] },
  { name: 'Content persistence', notes: ['write files, hold path locks,', 'enqueue derived work'] },
  { name: 'SemanticQueue', notes: ['L0 / L1 generation (model calls)'] },
  { name: 'EmbeddingQueue', notes: ['embed abstracts and content'] },
  { name: 'Vector index', notes: ['searchable records'] }
]
</script>

<template>
  <FlowDiagram
    title="Resource ingestion pipeline"
    desc="Input goes through the Parser, TreeBuilder, content persistence, the semantic queue and the embedding queue, and ends in the vector index."
    :steps="steps"
    :core="3"
  />
</template>
