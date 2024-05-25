import { defineStore } from 'pinia'
import axios from "axios"
import { socket } from "./captions"

interface LocationType {
  location: string;
  name: string;
  status: string;
}

interface Locations {
  [location: string]: LocationType
}

export const useLocationsStore = defineStore("locations", {
  state: () => {
    return {
      locations: {} as Locations,
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
    getLocation(location: string) {
      return this.locations.find((x) => x.location = location)
    },
    updateLocation(location: LocationType | undefined) {
      console.log(this.locations)
      if (this.locations) {
        return this.locations[location.location] = location
      }
      return undefined
    },
  },
})

socket.on("location", (location: LocationType) => {
  const store = useLocationsStore()
  store.updateLocation(location)
});
