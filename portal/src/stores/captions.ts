import { defineStore } from 'pinia'
import axios from "axios"
import { io } from "socket.io-client";

interface CaptionData {
  location: string,
  latest: Caption,
  captions: Caption[],
}

interface Caption {
  location: string;
  timestamp: string;
  text: string;
}

const dateSort = (a: Caption, b: Caption) => { return new Date(b.timestamp).valueOf() - new Date(a.timestamp).valueOf() }

export const socket = io(import.meta.env.VITE_API);

export const useCaptionsStore = defineStore("captions", {
  state: () => {
    return {
      captions: [] as CaptionData[],
      error: "" as string,
      connected: false,
    }
  },
  actions: {
    async fetchCaptions(location: string) {
      try {
        const data = await axios.get(import.meta.env.VITE_API + '/captions/' + location)
        this.captions = data.data
        this.error = ""
      } catch (error) {
        this.error = String(error)
      }
    },
    getData(location: string): CaptionData | undefined {
      return this.captions.find((x: CaptionData) => x.location == location)
    },
    getCaptions(location: string): Caption[] {
      const data = this.getData(location)
      if (data) {
        return data.captions.sort(dateSort)
      }
      return []
    },
    getLatest(location: string): Caption {
      const data = this.getData(location)
      if (data) {
        return data.latest
      }
      return { location: location, timestamp: "", text: "" }
    }
  },
})

//socket.on("bar", (...args) => {
//  useCaptionsStore.state.captions.push(args);
//});
