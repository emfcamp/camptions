import fetch from "node-fetch";
import storage from "node-persist";
import { socket } from "./index";

let streamConfig: Array<StreamConfig> = [
    {
        reference: "a",
        baseUrl: "https://www.streamtext.net/captions",
        event: "IHaveADream",
        language: "en",
        password: "testingPass",
    },
];

interface Stream {
    reference: string;
    config: StreamConfig;
    instance: StreamManager;
}

interface StreamConfig {
    reference: string;
    baseUrl: string;
    event: string;
    language: string;
    password: string;
}

interface StreamTextData {
    lastPosition: number;
    content: string;
    languageCode: string;
}

let allStreams: Array<Stream> = [];
let streamReferences: Array<string> = [];

function getStream(reference: string) {
    const stream = allStreams.filter(
        (x: Stream) => x.reference == reference
    )[0];
    return stream;
}

class StreamManager {
    config: StreamConfig;
    lastPosition: number;
    captionData: string;

    constructor(reference: string) {
        let config = streamConfig.filter(
            (x: StreamConfig) => x.reference == reference
        )[0];
        let stream = {
            reference: config.reference,
            config: config,
            instance: this,
        };
        allStreams.push(stream);
        streamReferences.push(reference);

        this.config = stream.config;

        this.lastPosition = 0;
        this.captionData = "";
        this.fetchCache();
        setInterval(() => {
            this.saveCache();
        }, 20000);
        setInterval(() => {
            this.pollStreamtext();
        }, 1000);
    }

    async fetchCache() {
        await storage.init();
        this.captionData = (await storage.getItem(this.config.reference)) || {};
        this.saveCache();
    }

    async saveCache() {
        await storage.setItem(this.config.reference, this.captionData);
    }

    async pollStreamtext() {
        const response = await fetch(
            // Length set to 10 for testing - should be set to very high number (e.g. 1000000)
            `${this.config.baseUrl}?event=${this.config.event}&language=${this.config.language}&last=${this.lastPosition}&length=32`,
            {
                headers: {
                    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
                    authorization: `Basic ${Buffer.from(
                        this.config.password
                    ).toString("base64")}`,
                },
                method: "GET",
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = (await response.json()) as StreamTextData;

        let captions: string = "";
        if (data.content.length !== 0) {
            captions = decodeURIComponent(data.content);
        }

        this.captionData = this.captionData + captions;
        this.lastPosition = data.lastPosition;

        console.log(data);

        socket.emit(this.config.reference, { new: captions });
    }
}

export {
    StreamManager,
    StreamConfig,
    streamConfig,
    streamReferences,
    getStream,
};
