<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const diaryText = ref('')
const diaryResult = ref(null)
const stamps = ref([])
const stampResult = ref(null)
const sending = ref(false)

onMounted(async () => {
  try { stamps.value = (await api.stamps()).stamps } catch (e) {}
})

async function submitDiary() {
  if (!diaryText.value.trim()) return
  sending.value = true
  diaryResult.value = null
  try {
    diaryResult.value = await api.writeDiary(diaryText.value)
    diaryText.value = ''
  } catch (e) {
    diaryResult.value = { error: e.message }
  } finally {
    sending.value = false
  }
}

async function submitStamp(icon, note = '') {
  sending.value = true
  stampResult.value = null
  try {
    stampResult.value = await api.writeStamp({ title: icon, text: note })
  } catch (e) {
    stampResult.value = { error: e.message }
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <div>
    <h1 style="margin-bottom: 24px">✏️ 写日记 / 盖章</h1>

    <!-- 日记区 -->
    <div class="card">
      <h3>📖 写点什么</h3>
      <textarea v-model="diaryText" rows="5" placeholder="今天发生了什么…"
                style="margin-bottom: 12px" :disabled="sending"></textarea>
      <button @click="submitDiary" :disabled="sending || !diaryText.trim()">
        {{ sending ? '写入中…' : '提交日记' }}
      </button>
      <div v-if="diaryResult" class="muted" style="margin-top: 10px">
        <span v-if="diaryResult.error" class="error">{{ diaryResult.error }}</span>
        <span v-else style="color: #7ee787">✓ 已写入</span>
      </div>
    </div>

    <!-- 情绪章区 -->
    <div class="card">
      <h3>📌 情绪章</h3>
      <div style="display: flex; flex-wrap: wrap; gap: 10px">
        <button v-for="s in stamps" :key="s.icon"
                @click="submitStamp(s.icon)"
                :disabled="sending"
                style="font-size: 20px; padding: 12px 16px; display: flex; flex-direction: column; align-items: center; gap: 4px">
          <span>{{ s.icon }}</span>
          <span style="font-size: 12px">{{ s.name }}</span>
        </button>
      </div>
      <div v-if="stampResult" class="muted" style="margin-top: 12px">
        <span v-if="stampResult.error" class="error">{{ stampResult.error }}</span>
        <span v-else style="color: #7ee787">✓ {{ stampResult.icon }} {{ stampResult.name }}（arousal: {{ stampResult.arousal }}）</span>
      </div>
    </div>
  </div>
</template>
