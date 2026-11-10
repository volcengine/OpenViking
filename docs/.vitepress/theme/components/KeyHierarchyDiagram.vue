<script setup>
import FlowDiagram from './FlowDiagram.vue'

const steps = [
  { name: 'Root Key', notes: ['one per OpenViking instance', 'local file, or KMS / Vault-wrapped'], edge: 'HKDF-SHA256 per account' },
  { name: 'Account Key (KEK)', notes: ['one per account, derived at runtime,', 'never stored'], edge: 'AES-256-GCM key wrap' },
  { name: 'File Key (DEK)', notes: ['random per write, stored wrapped', 'in the file envelope; encrypts content'] }
]
</script>

<template>
  <FlowDiagram
    title="Three-layer key hierarchy"
    desc="The root key, unique per instance and stored locally or wrapped by KMS or Vault, derives one account key per account with HKDF-SHA256. The account key wraps a random per-write file key with AES-256-GCM; the wrapped file key is stored in the file envelope and encrypts the content."
    :steps="steps"
    :core="0"
  />
</template>
