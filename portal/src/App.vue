<script setup lang="ts">
import { RouterView } from 'vue-router'
import { onMounted, computed } from 'vue'
import { useLocationsStore } from '@/stores/locations'
const store = useLocationsStore()
onMounted(() => {
  store.fetchLocations()
})
const error = computed(() => {
  return store.error
})
</script>

<template>
  <div>
    <RouterView />
    <div v-if="error" id="error-bar">Error Connecting to Server: {{ error }}</div>
  </div>
</template>

<style scoped>
</style>
