import storage from "node-persist";
import { io } from "./index";
import {
    locations,
    LocationType
} from "./locations";

interface Transcription {
    location: string;
    event: string;
    timestamp: string;
    text: string;
}

interface Caption {
    location: string;
    timestamp: string;
    text: string;
}

interface StreamInstance {
    location: string;
    config: LocationType;
    instance: StreamManager;
}

let allStreams: Array<StreamInstance> = [];
let streamReferences: Array<string> = [];

function getStream(location: string) {
    const stream = allStreams.filter(
        (x: StreamInstance) => x.location == location
    )[0];
    return stream;
}

class StreamManager {
    config: LocationType;
    latestCaption: Caption;
    captionData: Array<Caption>;

    constructor(location: string) {
        let config = locations.filter(
            (x: LocationType) => x.location == location
        )[0];
        let stream = {
            location: config.location,
            config: config,
            instance: this,
        };
        allStreams.push(stream);
        streamReferences.push(location);

        this.config = stream.config;

        this.latestCaption = {
            location: "",
            timestamp: "",
            text: "",
        };
        this.captionData = [];
        this.fetchCache();
        setInterval(() => {
            this.saveCache();
        }, 20000);
    }

    processTranscription(data: Transcription) {
        let newCaption: Caption = { location: data.location, timestamp: data.timestamp, text: data.text }
        if (data.event == "latest") {
            this.latestCaption = newCaption
            io.to(this.config.location).emit("latest", this.latestCaption)
        } else if (data.event == "segment") {
            this.captionData.push(newCaption)
            io.to(this.config.location).emit("add", newCaption)
        }
        return newCaption
    }

    async fetchCache() {
        await storage.init();
        this.captionData =
            (await storage.getItem(this.config.location)) || [];
        this.saveCache();
    }

    async saveCache() {
        await storage.setItem(
            this.config.location,
            this.captionData
        );
    }
}

export {
    StreamManager,
    Transcription,
    Caption,
    streamReferences,
    getStream,
};
