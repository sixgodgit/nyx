<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
const nav = [
  { path: '/', label: '今日扉页' },
  { path: '/memories', label: '记忆书' },
  { path: '/timeline', label: '故事线' },
  { path: '/persona', label: '理解文档' },
  { path: '/diary', label: '写日记' },
  { path: '/settings', label: '设置' },
]
</script>

<template>
  <div class="layout">
    <aside class="side">
      <div class="brand">🕯️ Nyx</div>
      <nav>
        <RouterLink v-for="n in nav" :key="n.path" :to="n.path"
          :class="route.path === n.path ? 'active' : ''">{{ n.label }}</RouterLink>
      </nav>
    </aside>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
