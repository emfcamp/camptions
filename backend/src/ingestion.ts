import storage from "node-persist";
import { io } from "./index";

let streamConfig: Array<StreamConfig> = [
    {
        location: "stage-a",
    },
    {
        location: "stage-b",
    },
    {
        location: "stage-c",
    },
];

interface StreamConfig {
    location: string;
}

interface Transcription {
    location: string;
    event: string;
    timestamp: string;
    text: string;
}

interface Caption {
    timestamp: string;
    text: string;
}

interface StreamInstance {
    location: string;
    config: StreamConfig;
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
    config: StreamConfig;
    latestCaption: Caption;
    captionData: Array<Caption>;

    constructor(location: string) {
        let config = streamConfig.filter(
            (x: StreamConfig) => x.location == location
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
        let newCaption: Caption = { timestamp: data.timestamp, text: data.text }
        if (data.event == "latest") {
            this.latestCaption = newCaption
            io.to(this.config.location).emit("latest", this.latestCaption)
        } else if (data.event == "segment") {
            this.captionData.push(newCaption)
            io.to(this.config.location).emit("next", newCaption)
        }
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
    StreamConfig,
    Transcription,
    streamConfig,
    streamReferences,
    getStream,
};
