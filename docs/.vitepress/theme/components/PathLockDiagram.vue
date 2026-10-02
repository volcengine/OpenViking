<script setup>
import FlowDiagram from './FlowDiagram.vue'

const steps = [
  { name: 'Service operation', notes: ['rm · mv · add_resource · session.commit'] },
  { name: 'Acquire a lease', notes: ['RAGFS PathLockManager', 'EXACT path or TREE subtree'] },
  { name: 'Modify covered paths', notes: ['files, then indexes and queues'] },
  { name: 'Release the lease', notes: ['or hand it to background processing'] }
]
</script>

<template>
  <FlowDiagram
    title="Path lock lifecycle"
    desc="A service operation acquires a lease from the RAGFS PathLockManager, modifies the covered paths while coordinating indexes and queues, then releases the lease or hands it to background processing."
    :steps="steps"
    :core="1"
  />
</template>
