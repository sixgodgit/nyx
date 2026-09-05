<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const today = ref(null)
const loading = ref(true)

onMounted(async () => {
  try {
    today.value = await api.today()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <h1 style="margin-bottom: 24px">🕯️ 今日扉页</h1>
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!today" class="error">无法连接服务</div>
    <div v-else>
      <!-- 统计概览 -->
      <div class="stat-row" style="margin-bottom: 24px">
        <div class="stat">
          <div class="num">{{ today.summary_stats.memories_last_24h }}</div>
          <div class="muted">近 24h 记忆</div>
        </div>
        <div class="stat">
          <div class="num">{{ today.summary_stats.engram_total }}</div>
          <div class="muted">Engram 总数</div>
        </div>
        <div class="stat">
          <div class="num">{{ today.summary_stats.open_loop_count }}</div>
          <div class="muted">未闭合线索</div>
        </div>
      </div>

      <!-- 人格画像摘录 -->
      <div class="card">
        <h3>🧠 AI 对你的理解</h3>
        <pre style="white-space: pre-wrap; font-size: 14px; color: var(--muted)">{{ today.persona_excerpt }}</pre>
      </div>

      <!-- 未闭合线索 -->
      <div class="card" v-if="today.open_loops.length">
        <h3>⚡ 未闭合线索</h3>
        <div v-for="(loop, i) in today.open_loops" :key="i" class="list-item">
          <span class="tag" :class="loop.type">{{ loop.type }}</span>
          <span>{{ loop.content.slice(0, 120) }}…</span>
        </div>
      </div>

      <!-- 最近记忆 -->
      <div class="card">
        <h3>📖 最近记忆</h3>
        <div v-for="(m, i) in today.recent_memories" :key="i" class="list-item">
          <span class="muted">{{ m.ts.split('T')[0] }}</span>  {{ m.text }}
        </div>
      </div>

      <!-- 最近情绪 -->
      <div class="card" v-if="today.recent_emotions.length">
        <h3>🌊 近期情绪</h3>
        <div v-for="(e, i) in today.recent_emotions" :key="i" class="list-item">
          <span class="muted">{{ e.ts }}</span>  {{ e.mood }}
        </div>
      </div>
    </div>
  </div>
</template>
