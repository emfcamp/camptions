<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useLocationsStore } from '@/stores/locations'
import { useCaptionsStore } from '@/stores/captions'
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
onMounted(() => {
  captionStore.fetchCaptions(loc)
})
</script>

<template>
  <main class="page">
    <router-link class="back" :to="{ name: 'index' }">back</router-link>
    <div v-if="location">
      <h1>{{ location.name }} Captions</h1>
      <div v-if="captions.length | latest.text.length">
        <p v-for="caption in captions" v-bind:key="caption.timestamp">{{ caption.text }}</p>
        <p>{{ latest.text }}</p>
      </div>
      <div v-else>
        <p>There are currently no captions available.</p>
        <p>Please contact the Duty Technician on XXXX if you think there is a problem.</p>
      </div>
    </div>
    <div v-else></div>
  </main>
</template>
