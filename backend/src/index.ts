import express, { Request, Response } from "express";
import { createServer } from "http";
import { Server } from "socket.io";
import {
    StreamManager,
    StreamConfig,
    streamConfig,
    streamReferences,
    getStream,
} from "./streams";
const app = express();

app.get("/", async (req: Request, res: Response) => {
    res.send("emf-camptions service");
});

// Initialise all stream polling
streamConfig.forEach((x: StreamConfig) => new StreamManager(x.reference));

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
const socket = new Server(httpServer, {
    allowEIO3: true,
    cors: {
        origin: process.env.CORS_URI,
        methods: ["GET", "POST"],
    },
});

httpServer.listen(process.env.PORT || 3000);

export { socket };
