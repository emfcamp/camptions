import { createRouter, createWebHistory } from 'vue-router'
import IndexView from '@/views/IndexView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'index',
      component: IndexView
    },
    {
      path: '/live/:location',
      name: 'captions',
      component: () => import('@/views/CaptionsView.vue')
    },
    {
      path: '/screen/stage/:location',
      name: 'stage-screen',
      component: () => import('@/views/StageScreen.vue')
    },
    {
      path: '/screen/captions/:location',
      name: 'captions-screen',
      component: () => import('@/views/CaptionsScreen.vue')
    }
  ]
})

export default router
