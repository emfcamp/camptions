<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useLocationsStore } from '@/stores/locations'
import { useCaptionsStore } from '@/stores/captions'
import { onBeforeRouteLeave } from 'vue-router'
import ThemeToggle from '@/components/ThemeToggle.vue'
const route = useRoute()
const locationStore = useLocationsStore()
const captionStore = useCaptionsStore()
const loc = String(route.params.location)
const location = computed(() => {
  return locationStore.getLocation(loc)
})
const captions = computed(() => {
  return captionStore.getCaptions(loc)
})
const latest = computed(() => {
  return captionStore.getLatest(loc)
})
onMounted(async () => {
  captionStore.fetchCaptions(loc)
  captionStore.joinRoom(loc)
})
onBeforeRouteLeave((to, from) => {
  captionStore.leaveRoom(loc)
})
</script>

<template>
  <main class="page">
    <div v-if="location">
      <div v-if="!captions.length && !latest.text">
        <p>There are currently no captions available.</p>
        <p>Please contact the Duty Technician on 1075 if you think there is a problem.</p>
      </div>
      <div v-else class="captionboxscreen" ref="captionBox">
        <span v-if="latest">{{ latest.text }}</span>
        <span v-for="caption in captions" v-bind:key="caption.timestamp">{{ caption.text }}</span>
      </div>
      <div class="qrbox">
        <p>Scan QR code to see these captions on your personal device</p>
      </div>
    </div>
    <div v-else></div>
  </main>
</template>
