<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const health = ref(null)
const loading = ref(true)

onMounted(async () => {
  try { health.value = await api.health() } catch (e) {} finally { loading.value = false }
})
</script>

<template>
  <div>
    <h1 style="margin-bottom: 24px">⚙️ 设置</h1>
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="!health" class="error">服务不可达</div>
    <div v-else>
      <div class="card">
        <h3>引擎状态</h3>
        <div class="list-item">状态: <span style="color: #7ee787">{{ health.status }}</span></div>
        <div class="list-item">数据目录: {{ health.base }}</div>
        <div class="list-item">服务时间: {{ health.time }}</div>
      </div>
      <div class="card">
        <h3>关于</h3>
        <div class="list-item">版本: nyx_server v0.1.0</div>
        <div class="list-item">前端: nyx-web v0.1.0</div>
        <div class="list-item">地址: <code>memory.hvh.expert</code></div>
      </div>
    </div>
  </div>
</template>
