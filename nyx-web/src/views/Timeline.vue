<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const memories = ref([])
const loading = ref(true)
const emotions = ref([])
const groups = ref([])

onMounted(async () => {
  try {
    const [memRes, emoRes] = await Promise.all([
      api.memories({ limit: 300 }),
      api.emotions(100),
    ])
    memories.value = memRes.items
    emotions.value = emoRes.items
    // 按日期分组
    const map = {}
    for (const m of memRes.items) {
      const day = (m.ts || m.created_at || '').slice(0, 10)
      if (!day) continue
      if (!map[day]) map[day] = []
      map[day].push(m)
    }
    groups.value = Object.entries(map).sort(([a], [b]) => b.localeCompare(a))
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})

function emotionOnDay(day) {
  return emotions.value.filter(e => e.ts && e.ts.startsWith(day))
}
</script>

<template>
  <div>
    <h1 style="margin-bottom: 16px">⏱️ 故事线</h1>
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!groups.length" class="muted">暂无记忆</div>
    <div v-else>
      <div v-for="([day, items], gi) in groups" :key="gi" class="card">
        <h3 style="display: flex; align-items: center; gap: 10px">
          <span>📅 {{ day }}</span>
          <span v-for="(emo, ei) in emotionOnDay(day)" :key="ei"
                style="font-size: 14px; color: var(--accent)">🌊 {{ emo.mood }}</span>
        </h3>
        <div v-for="(item, mi) in items" :key="mi" class="list-item" style="padding-left: 20px">
          <span v-if="item.type" class="tag" :class="item.type">{{ item.type }}</span>
          <span>{{ item.content || item.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
