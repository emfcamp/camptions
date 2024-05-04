import express, { Request, Response } from "express";
import { createServer } from "http";
import { Server } from "socket.io";
import {
    StreamManager,
    StreamConfig,
    Transcription,
    streamConfig,
    streamReferences,
    getStream,
} from "./ingestion";
const app = express();

app.get("/", async (req: Request, res: Response) => {
    res.send("emf-camptions service");
});

// Initialise all stream polling
streamConfig.forEach((x: StreamConfig) => new StreamManager(x.location));

app.get("/stream/:reference", async (req: Request, res: Response) => {
    try {
        if (!streamReferences.includes(req.params.reference)) {
            return res.sendStatus(404);
        }

        const captions = getStream(req.params.reference).instance.captionData;

        let latest = 200;
        if (req.query.latest != undefined) {
            latest = Number(req.query.latest) as number;
            if (latest > captions.length || latest < 0) {
                latest = captions.length;
            }
        }

        res.json(captions.slice(-latest));
    } catch (error: any) {
        res.status(500).send(error.message);
    }
});

const httpServer = createServer(app);
const io = new Server(httpServer, {
        allowEIO3: true,
        cors: {
            origin: process.env.CORS_URI,
            methods: ["GET", "POST"],
        },
    });

io.on('connection', (socket) => {
    socket.on('transcription', (data: string) => {
        let transcription: Transcription = JSON.parse(data)
        if (streamReferences.includes(transcription.location)) {
            getStream(transcription.location).instance.processTranscription(transcription)
        }
    });
});

httpServer.listen(process.env.PORT || 3000);

export { io };
