import { defineStore } from 'pinia'
import axios from "axios"

interface LocationType {
  location: string;
  name: string;
}

export const useLocationsStore = defineStore("locations", {
  state: () => {
    return {
      locations: [] as LocationType[],
      error: "" as string,
    }
  },
  getters: {
    getLocations(state) {
      return state.locations
    },
  },
  actions: {
    async fetchLocations() {
      try {
        const data = await axios.get(import.meta.env.VITE_API + '/locations')
        this.locations = data.data
        this.error = ""
      } catch (error) {
        this.error = String(error)
      }
    },
    getLocation(location: String) {
      return this.locations.find((x: LocationType) => x.location == location)
    }
  },
})