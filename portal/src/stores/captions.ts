import { defineStore } from 'pinia'
import axios from "axios"
import { io } from "socket.io-client";
import { useLocationsStore } from '@/stores/locations'

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

const dateSort = (a: Caption, b: Caption) => { return new Date(a.timestamp).valueOf() - new Date(b.timestamp).valueOf() }

export const socket = io(import.meta.env.VITE_WS);

export const useCaptionsStore = defineStore("captions", {
  state: () => {
    return {
      captions: {} as AllCaptions,
      latest: {} as LatestCaptions,
      error: "" as string,
      connection: "no-backend" as string,
      room: "",
    }
  },
  getters: {
    getRoom(state) {
      return state.room
    },
    getConnection(state) {
      return state.connection
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
  const store = useCaptionsStore()
  store.connection = "no-backend"
});

socket.on("latest", (caption: Caption) => {
  const store = useCaptionsStore()
  store.setLatest(caption)
});

socket.on("add", (caption: Caption) => {
  const store = useCaptionsStore()
  store.addCaption(caption)
});

const doConnect = () => {
  const store = useCaptionsStore()
  const locStore = useLocationsStore()
  locStore.fetchLocations()
  if (store.getRoom) {
    store.fetchCaptions(store.getRoom)
    socket.emit("join", store.getRoom)
  }
  store.connection = "connected"
}

socket.on("reconnect", doConnect);

socket.on("connect", doConnect);

socket.on("disconnect", () => {
  const store = useCaptionsStore()
  store.connection = "no-backend"
});