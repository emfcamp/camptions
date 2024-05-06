<script setup lang="ts">
import { computed } from 'vue'
import { useLocationsStore } from '@/stores/locations'
const store = useLocationsStore()
const locations = computed(() => {
  return store.getLocations
})
</script>

<template>
  <main class="page">
    <h3>EMFCamp Captions</h3>
    <div class="links" v-for="location in locations" v-bind:key="location.location">
      <router-link class="link" :to="{ name: 'captions', params: { location: location.location } }">
        {{ location.name }}
      </router-link>
    </div>
  </main>
</template>
