<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

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
    const map = {}
    for (const m of memRes.items) {
      const day = (m.ts || m.created_at || '').slice(0, 10)
      if (!day) continue
      if (!map[day]) map[day] = []
      map[day].push(m)
    }
    groups.value = Object.entries(map).sort(([a], [b]) => b.localeCompare(a))
  } catch (e) { console.error(e) }
  finally { loading.value = false }
})

function emotionOnDay(day) {
  return emotions.value.filter(e => e.ts && e.ts.startsWith(day))
}
</script>

<template>
  <div>
    <div v-if="loading" class="loading"><span class="spin"></span>加载中</div>
    <div v-else-if="!groups.length" class="muted">暂无记忆</div>
    <div v-else>
      <div v-for="([day, items], gi) in groups" :key="gi" class="card tl-day">
        <span class="tl-node"></span>
        <div class="tl-head">
          <MbIcon name="clock" :size="15" style="color: var(--accent-deep)" />
          <h3 style="margin: 0; font-size: 16px; font-family: var(--mono)">{{ day }}</h3>
          <span v-for="(emo, ei) in emotionOnDay(day)" :key="ei" class="tag emotional">
            {{ emo.mood }}
          </span>
        </div>
        <div v-for="(item, mi) in items" :key="mi" class="list-item">
          <span v-if="item.type" class="tag" :class="item.type">{{ item.type }}</span>
          <span class="body">{{ item.content || item.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
