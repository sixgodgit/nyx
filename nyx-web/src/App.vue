<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import MbIcon from './components/MbIcon.vue'

const route = useRoute()
const nav = [
  { path: '/', label: '今日扉页', icon: 'home' },
  { path: '/memories', label: '记忆书', icon: 'book' },
  { path: '/timeline', label: '故事线', icon: 'timeline' },
  { path: '/persona', label: '理解文档', icon: 'persona' },
  { path: '/diary', label: '写日记', icon: 'pen' },
  { path: '/settings', label: '设置', icon: 'gear' },
]
const meta = computed(() => nav.find(n => n.path === route.path) || nav[0])
</script>

<template>
  <div class="layout">
    <aside class="side">
      <div class="brand">
        <div class="logo"><MbIcon name="brand" :size="19" /></div>
        <div>
          <div>Nyx</div>
          <div class="sub">Memory Book</div>
        </div>
      </div>
      <nav>
        <RouterLink v-for="n in nav" :key="n.path" :to="n.path"
          class="nav-item" :class="{ active: route.path === n.path }">
          <span class="nav-ic"><MbIcon :name="n.icon" :size="18" /></span>
          {{ n.label }}
        </RouterLink>
      </nav>
      <div class="side-foot">
        <span><span class="dot">●</span> night cognition</span>
        <span>memory.hvh.expert</span>
      </div>
    </aside>
    <main class="content">
      <div class="page-head">
        <div class="ph-ic"><MbIcon :name="meta.icon" :size="18" /></div>
        <h1>{{ meta.label }}</h1>
      </div>
      <RouterView />
    </main>
  </div>
</template>
