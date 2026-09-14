import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'
import Memories from './views/Memories.vue'
import Timeline from './views/Timeline.vue'
import Persona from './views/Persona.vue'
import Diary from './views/Diary.vue'
import Settings from './views/Settings.vue'

const routes = [
  { path: '/', component: Home },
  { path: '/memories', component: Memories },
  { path: '/timeline', component: Timeline },
  { path: '/persona', component: Persona },
  { path: '/diary', component: Diary },
  { path: '/settings', component: Settings },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
