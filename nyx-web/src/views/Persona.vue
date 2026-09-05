<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const persona = ref(null)
const loading = ref(true)

onMounted(async () => {
  try {
    persona.value = await api.persona()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <h1 style="margin-bottom: 24px">🧠 理解文档</h1>
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!persona?.raw_md" class="muted">画像尚未生成</div>
    <div v-else class="card">
      <pre style="white-space: pre-wrap; font-size: 14px; line-height: 1.7; color: var(--text)">{{ persona.raw_md }}</pre>
    </div>
  </div>
</template>
