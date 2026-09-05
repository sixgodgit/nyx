<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

const memories = ref([])
const loading = ref(true)
const typeFilter = ref('')
const total = ref(0)
const page = ref(0)
const limit = 50

const types = ['', 'semantic', 'episodic', 'emotional', 'procedural']
const typeLabel = { '': '全部', semantic: '语义', episodic: '情景', emotional: '情感', procedural: '程序' }

async function load() {
  loading.value = true
  try {
    const params = { limit, offset: page.value * limit }
    if (typeFilter.value) params.type = typeFilter.value
    const res = await api.memories(params)
    memories.value = res.items
    total.value = res.total
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}
onMounted(load)
watch(typeFilter, () => { page.value = 0; load() })
function prev() { if (page.value > 0) { page.value--; load() } }
function next() { if ((page.value + 1) * limit < total.value) { page.value++; load() } }
</script>

<template>
  <div>
    <div class="filters">
      <button v-for="t in types" :key="t" @click="typeFilter = t"
        :class="typeFilter === t ? 'selected' : 'pill'">{{ typeLabel[t] }}</button>
    </div>

    <div class="muted" style="margin-bottom: 16px">
      {{ total }} 条记录 · 第 {{ page + 1 }} 页
    </div>

    <div v-if="loading" class="loading"><span class="spin"></span>加载中</div>
    <div v-else-if="!memories.length" class="muted">暂无记录</div>
    <div v-else class="card" style="padding: 14px 20px">
      <div v-for="(m, i) in memories" :key="i" class="list-item">
        <span class="ts">{{ (m.ts || m.created_at || '').slice(0, 16) }}</span>
        <span v-if="m.type" class="tag" :class="m.type">{{ m.type }}</span>
        <span class="body">{{ m.content || m.text }}</span>
      </div>
    </div>

    <div style="display: flex; gap: 12px; margin-top: 8px">
      <button @click="prev" :disabled="page === 0" class="ghost">
        <MbIcon name="arrowL" :size="15" />上一页
      </button>
      <button @click="next" :disabled="(page + 1) * limit >= total" class="ghost">
        下一页<MbIcon name="arrowR" :size="15" />
      </button>
    </div>
  </div>
</template>
