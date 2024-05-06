<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useLocationsStore } from '@/stores/locations'
const route = useRoute()
const store = useLocationsStore()
const location = computed(() => {
  return store.getLocation(route.params.location)
})
</script>

<template>
  <main class="page">
    <router-link class="back" :to="{ name: 'index' }">back</router-link>
    <div class="about">
      <h1>{{ location.name }} Captions</h1>
    </div>
  </main>
</template>

