import { defineStore } from 'pinia'
import axios from "axios"
import { io } from "socket.io-client";

interface AllCaptions {
  [location: string]: Caption[]
}

interface LatestCaptions {
  [location: string]: Caption
}

interface Caption {
  location: string;
  timestamp: string;
  text: string;
}

const dateSort = (a: Caption, b: Caption) => { return new Date(b.timestamp).valueOf() - new Date(a.timestamp).valueOf() }

export const socket = io(import.meta.env.VITE_WS);

export const useCaptionsStore = defineStore("captions", {
  state: () => {
    return {
      captions: {} as AllCaptions,
      latest: {} as LatestCaptions,
      error: "" as string,
      connected: false,
      room: "",
    }
  },
  getters: {
    getRoom(state) {
      return state.room
    }
  },
  actions: {
    async fetchCaptions(location: string) {
      try {
        const data = await axios.get(import.meta.env.VITE_API + '/captions/' + location)
        this.captions[location] = data.data
        this.error = ""
      } catch (error) {
        this.error = String(error)
      }
    },
    getCaptions(location: string): Caption[] {
      if (location in this.captions) {
        return this.captions[location].sort(dateSort)
      }
      return []
    },
    getLatest(location: string): Caption {
      if (location in this.latest) {
        return this.latest[location]
      }
      return { location: location, timestamp: "", text: "" }
    },
    addCaption(caption: Caption) {
      this.captions[caption.location].push(caption)
    },
    setLatest(caption: Caption) {
      this.latest[caption.location] = caption
    },
    joinRoom(location: string) {
      this.room = location
      socket.emit("join", location)
    },
    leaveRoom(location: string) {
      this.room = ""
      socket.emit("leave", location)
    }
  },
})

socket.on("connect_error", (err) => {
  console.log(`connect_error due to ${err.message}`);
});

socket.on("latest", (caption: Caption) => {
  const store = useCaptionsStore()
  store.setLatest(caption)
});

socket.on("add", (caption: Caption) => {
  const store = useCaptionsStore()
  store.addCaption(caption)
});

socket.on("reconnect", () => {
  const room = useCaptionsStore().getRoom
  if (room) {
    socket.emit("join", room)
  }
});
