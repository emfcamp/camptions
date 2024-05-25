<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'
const store = useThemeStore()
</script>
<template>
    <a href="#" class="link-btn theme" @click="store.toggleTheme">
      <div class="link">
        <div>
          <div class="link-icon">{{ store.getToggleTheme == "light" ? "☀️" : "🌙" }}</div>Theme
        </div>
      </div>
    </a>
</template>
