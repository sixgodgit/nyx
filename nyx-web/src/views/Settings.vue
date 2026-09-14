<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

const health = ref(null)
const loading = ref(true)
onMounted(async () => {
  try { health.value = await api.health() } catch (e) {} finally { loading.value = false }
})
</script>

<template>
  <div>
    <div v-if="loading" class="loading"><span class="spin"></span>加载中</div>
    <div v-else-if="!health" class="error">服务不可达</div>
    <div v-else>
      <div class="card">
        <h3><span class="h-ic"><MbIcon name="spark" :size="15" /></span>引擎状态
          <span class="h-tag">live</span></h3>
        <div class="list-item">
          <span class="ts">status</span>
          <span class="body"><span class="ok">●</span> {{ health.status }}</span>
        </div>
        <div class="list-item">
          <span class="ts">base</span>
          <span class="body mono"><code class="inline">{{ health.base }}</code></span>
        </div>
        <div class="list-item">
          <span class="ts">time</span>
          <span class="body">{{ health.time }}</span>
        </div>
      </div>
      <div class="card">
        <h3><span class="h-ic"><MbIcon name="gear" :size="15" /></span>关于
          <span class="h-tag">about</span></h3>
        <div class="list-item"><span class="ts">server</span><span class="body">nyx_server v0.1.0</span></div>
        <div class="list-item"><span class="ts">front</span><span class="body">nyx-web · night edition</span></div>
        <div class="list-item"><span class="ts">addr</span><span class="body"><code class="inline">memory.hvh.expert</code></span></div>
      </div>
    </div>
  </div>
</template>
