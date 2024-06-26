import { defineStore } from 'pinia'

const localStorageName = "captionsTheme"

const getMediaPreference = () => {
  const rememberedTheme = localStorage.getItem(localStorageName);
  if (rememberedTheme && ["light", "dark"].includes(rememberedTheme)) {
    console
    return rememberedTheme
  }
  const hasDarkPreference = window.matchMedia(
    "(prefers-color-scheme: dark)"
  ).matches;
  if (hasDarkPreference) {
    return "dark";
  } else {
    return "light";
  }
}


export const useThemeStore = defineStore("theme", {
  state: () => {
    return {
      theme: getMediaPreference()
    }
  },
  getters: {
    getTheme(state) {
      return state.theme
    },
    getToggleTheme(state) {
      return state.theme == "dark" ? "light" : "dark"
    }
  },
  actions: {
    setTheme(darkMode: boolean) {
      this.theme = darkMode ? "dark" : "light"
      localStorage.setItem(localStorageName, this.theme)
    },
    toggleTheme() {
      this.setTheme(this.theme !== "dark")
    }
  },
})
