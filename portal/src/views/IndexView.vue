<script setup lang="ts">
import { computed } from 'vue'
import { useLocationsStore } from '@/stores/locations'
const store = useLocationsStore()
const locations = computed(() => {
  return store.getLocations
})
import ThemeToggle from '@/components/ThemeToggle.vue';
</script>

<template>
  <main class="page bg">
    <header>
      <h3>EMFCamp Captions</h3>
      <p class="header">To get captions right from your seat, choose a venue below.</p>
    </header>
    <div class="links">
      <div v-for="location in locations" v-bind:key="location.location">
        <router-link class="link-btn" :to="{ name: 'captions', params: { location: location.location } }">
          <div class="link venue">
            <div class="link-icon"><img v-bind:src="'/location-' + location.location + '.png'"></div>{{ location.name }}
          </div>
        </router-link>
      </div>
      <p class="theme">Or toggle theme!</p>
      <theme-toggle class="index-theme"></theme-toggle>
    </div>
  </main>
</template>
