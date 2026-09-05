<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import MbIcon from '../components/MbIcon.vue'

const diaryText = ref('')
const diaryResult = ref(null)
const stamps = ref([])
const stampResult = ref(null)
const sending = ref(false)

onMounted(async () => {
  try { stamps.value = (await api.stamps()).stamps } catch (e) {}
})

function waxTone(a) {
  // arousal 0.3–0.9 → 在朱砂/赭石间微调,保持印章暖调统一
  const t = Math.max(0, Math.min(1, (a - 0.3) / 0.6))
  // 从深赭 #a4622c 渐到深朱砂 #a83328
  const from = { r: 164, g: 98, b: 44 }
  const to = { r: 168, g: 51, b: 40 }
  const c = {
    r: Math.round(from.r + (to.r - from.r) * t),
    g: Math.round(from.g + (to.g - from.g) * t),
    b: Math.round(from.b + (to.b - from.b) * t),
  }
  return `rgb(${c.r},${c.g},${c.b})`
}

async function submitDiary() {
  if (!diaryText.value.trim()) return
  sending.value = true
  diaryResult.value = null
  try {
    diaryResult.value = await api.writeDiary(diaryText.value)
    diaryText.value = ''
  } catch (e) { diaryResult.value = { error: e.message } }
  finally { sending.value = false }
}

async function submitStamp(stamp) {
  sending.value = true
  stampResult.value = null
  try {
    stampResult.value = await api.writeStamp({ title: stamp.icon, text: stamp.name })
  } catch (e) { stampResult.value = { error: e.message } }
  finally { sending.value = false }
}
</script>

<template>
  <div>
    <!-- 日记区 -->
    <div class="card">
      <h3><span class="h-ic"><MbIcon name="pen" :size="15" /></span>写点什么
        <span class="h-tag">diary</span></h3>
      <textarea v-model="diaryText" rows="5" placeholder="今天发生了什么…" style="margin-bottom: 14px"
        :disabled="sending"></textarea>
      <div style="display:flex; justify-content:flex-end">
        <button class="primary" @click="submitDiary" :disabled="sending || !diaryText.trim()">
          <MbIcon name="check" :size="15" />{{ sending ? '写入中' : '提交日记' }}
        </button>
      </div>
      <div v-if="diaryResult" class="muted" style="margin-top: 12px">
        <span v-if="diaryResult.error" class="error">{{ diaryResult.error }}</span>
        <span v-else class="ok">已写入沙漏</span>
      </div>
    </div>

    <!-- 情绪章 -->
    <div class="card">
      <h3><span class="h-ic"><MbIcon name="seal" :size="15" /></span>情绪章
        <span class="h-tag">arousal</span></h3>
      <div v-if="!stamps.length" class="muted">暂无可盖章情绪</div>
      <div v-else class="stamp-grid">
        <button v-for="s in stamps" :key="s.name" class="stamp-btn"
          @click="submitStamp(s)" :disabled="sending">
          <span class="seal" :style="{ background: waxTone(s.arousal) }"><MbIcon name="seal" :size="22" /></span>
          <span class="sname">{{ s.name }}</span>
          <span class="arou">A {{ s.arousal.toFixed(1) }}</span>
        </button>
      </div>
      <div v-if="stampResult" class="muted" style="margin-top: 14px">
        <span v-if="stampResult.error" class="error">{{ stampResult.error }}</span>
        <span v-else class="ok">
          已盖下「{{ stampResult.name }}」章 · arousal {{ stampResult.arousal }}
        </span>
      </div>
    </div>
  </div>
</template>
