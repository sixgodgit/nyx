<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../api.js'

const memories = ref([])
const loading = ref(true)
const typeFilter = ref('')
const total = ref(0)
const page = ref(0)
const limit = 50

async function load() {
  loading.value = true
  try {
    const params = { limit, offset: page.value * limit }
    if (typeFilter.value) params.type = typeFilter.value
    const res = await api.memories(params)
    memories.value = res.items
    total.value = res.total
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(typeFilter, () => { page.value = 0; load() })

function prev() { if (page.value > 0) { page.value--; load() } }
function next() { if ((page.value + 1) * limit < total.value) { page.value++; load() } }
</script>

<template>
  <div>
    <h1 style="margin-bottom: 16px">📚 记忆书</h1>

    <!-- 类型筛选 -->
    <div style="display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap">
      <button @click="typeFilter = ''" :style="!typeFilter ? 'background: var(--accent)' : ''">全部</button>
      <button @click="typeFilter = 'semantic'" :style="typeFilter==='semantic' ? 'background: var(--accent)' : ''">semantic</button>
      <button @click="typeFilter = 'episodic'" :style="typeFilter==='episodic' ? 'background: var(--accent)' : ''">episodic</button>
      <button @click="typeFilter = 'emotional'" :style="typeFilter==='emotional' ? 'background: var(--accent)' : ''">emotional</button>
      <button @click="typeFilter = 'procedural'" :style="typeFilter==='procedural' ? 'background: var(--accent)' : ''">procedural</button>
    </div>

    <div class="muted" style="margin-bottom: 12px">共 {{ total }} 条记录（第 {{ page + 1 }} 页）</div>

    <div v-if="loading" class="muted">加载中…</div>
    <div v-else>
      <div v-for="(m, i) in memories" :key="i" class="list-item">
        <span class="muted" style="font-size: 12px">{{ m.ts || m.created_at }}</span>
        <span v-if="m.type" class="tag" :class="m.type" style="margin-left: 8px">{{ m.type }}</span>
        <span style="margin-left: 8px">{{ m.content || m.text }}</span>
      </div>
    </div>

    <div style="display: flex; gap: 12px; margin-top: 16px">
      <button @click="prev" :disabled="page===0">← 上一页</button>
      <button @click="next" :disabled="(page+1)*limit>=total">下一页 →</button>
    </div>
  </div>
</template>
