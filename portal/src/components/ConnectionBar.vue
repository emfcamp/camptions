<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useLocationsStore } from '@/stores/locations'
import { useCaptionsStore } from '@/stores/captions'
const route = useRoute()
const locationStore = useLocationsStore()
const captionStore = useCaptionsStore()
const loc = String(route.params.location)
const locStatus = computed(() => {
  return locationStore.getLocation(loc).status
})
const connection = computed(() => {
  return captionStore.getConnection
})
</script>
<template>
  <div id="connection-bar" v-if="connection != 'connected' || locStatus != 'connected'">
    There's a bit of a problem at the moment... ({{ connection }}
    <span v-if="connection == 'connected'"> but {{ locStatus }}</span
    >)
  </div>
</template>
