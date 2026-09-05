<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

const today = ref(null)
const loading = ref(true)

onMounted(async () => {
  try { today.value = await api.today() }
  catch (e) { console.error(e) }
  finally { loading.value = false }
})

const statDefs = [
  { key: 'memories_last_24h', label: '近 24h 记忆', icon: 'memory' },
  { key: 'engram_total', label: 'Engram 总数', icon: 'book' },
  { key: 'open_loop_count', label: '未闭合线索', icon: 'loop' },
]
</script>

<template>
  <div>
    <div v-if="loading" class="loading"><span class="spin"></span>加载中</div>
    <div v-else-if="!today" class="error">无法连接服务</div>
    <template v-else>
      <!-- 统计概览 -->
      <div class="stat-row">
        <div v-for="(s, i) in statDefs" :key="i" class="stat">
          <div class="stat-label"><MbIcon :name="s.icon" :size="12" style="vertical-align:-1px" /> {{ s.label }}</div>
          <div class="num">{{ today.summary_stats[s.key] }}</div>
        </div>
      </div>

      <!-- 人格画像摘录 -->
      <div class="card">
        <h3><span class="h-ic"><MbIcon name="persona" :size="15" /></span>AI 对你的理解
          <span class="h-tag">persona</span></h3>
        <pre class="mono-block">{{ today.persona_excerpt }}</pre>
      </div>

      <!-- 未闭合线索 -->
      <div v-if="today.open_loops.length" class="card">
        <h3><span class="h-ic"><MbIcon name="loop" :size="15" /></span>未闭合线索
          <span class="h-tag">{{ today.open_loops.length }}</span></h3>
        <div v-for="(loop, i) in today.open_loops" :key="i" class="list-item">
          <span class="tag" :class="loop.type">{{ loop.type }}</span>
          <span class="body">{{ loop.content.slice(0, 140) }}…</span>
        </div>
      </div>

      <!-- 最近记忆 -->
      <div class="card">
        <h3><span class="h-ic"><MbIcon name="book" :size="15" /></span>最近记忆
          <span class="h-tag">{{ today.recent_memories.length }}</span></h3>
        <div v-for="(m, i) in today.recent_memories" :key="i" class="list-item">
          <span class="ts">{{ m.ts.split('T')[0] }}</span>
          <span class="body">{{ m.text }}</span>
        </div>
      </div>

      <!-- 近期情绪 -->
      <div v-if="today.recent_emotions.length" class="card">
        <h3><span class="h-ic"><MbIcon name="wave" :size="15" /></span>近期情绪
          <span class="h-tag">{{ today.recent_emotions.length }}</span></h3>
        <div v-for="(e, i) in today.recent_emotions" :key="i" class="list-item">
          <span class="ts">{{ e.ts }}</span>
          <span class="body">{{ e.mood }}</span>
        </div>
      </div>
    </template>
  </div>
</template>
