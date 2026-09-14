<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

const persona = ref(null)
const loading = ref(true)

onMounted(async () => {
  try { persona.value = await api.persona() }
  catch (e) { console.error(e) }
  finally { loading.value = false }
})
</script>

<template>
  <div>
    <div v-if="loading" class="loading"><span class="spin"></span>加载中</div>
    <div v-else-if="!persona?.raw_md" class="muted">画像尚未生成</div>
    <div v-else class="card">
      <h3>
        <span class="h-ic"><MbIcon name="persona" :size="15" /></span>
        Night-cognition persona
        <span class="h-tag">raw</span>
      </h3>
      <pre class="mono-block" style="color: var(--text-2)">{{ persona.raw_md }}</pre>
    </div>
  </div>
</template>
