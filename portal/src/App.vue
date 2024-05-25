<script setup lang="ts">
import { RouterView } from 'vue-router'
import { onMounted, computed, watch } from 'vue'
import { useLocationsStore } from '@/stores/locations'
import { useThemeStore } from '@/stores/theme'
const store = useLocationsStore()
const theme = useThemeStore()
onMounted(() => {
  store.fetchLocations()
})
const state = computed(() => {
  return {
    error: store.error,
    theme: theme.theme
  }
})
</script>

<template>
  <div id="root" :class="state.theme">
    <RouterView />
    <div v-if="state.error" id="error-bar">Error Connecting to Server: {{ state.error }}</div>
  </div>
</template>

<style scoped>
</style>
