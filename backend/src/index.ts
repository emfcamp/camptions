import express, { Request, Response } from "express";
import cors from "cors";
import { createServer } from "http";
import { Server } from "socket.io";

import {
    locations,
    LocationType,
    ServerState,
    Heartbeat,
    updateStatus,
    heartBeat,
} from "./locations";

import {
    StreamManager,
    Transcription,
    Caption,
    streamReferences,
    getStream,
} from "./ingestion";

const app = express();
app.use(cors<Request>());

app.get("/", async (req: Request, res: Response) => {
    res.send("emf-camptions service");
});

app.get("/locations", async (req: Request, res: Response) => {
    res.json(locations);
});

app.get("/captions/:location", async (req: Request, res: Response) => {
    if (getStream(req.params.location)) {
        const captions = getStream(req.params.location).instance.captionData
        res.json(captions);
    } else res.sendStatus(404)
});

Object.values(locations).forEach((x: LocationType) => new StreamManager(x.location));

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
        origin: "http://localhost:5173",
        methods: ["GET", "POST"],
    },
});

io.use((socket, next) => {
    const token = socket.handshake.auth.token;
    if (token == process.env["TOKEN"]) {
        socket.user = "authenticated";
    }
    next();
});

io.on('connection', (socket) => {
    socket.on('transcription', (data: string) => {
        if (socket.user != "authenticated") return
        let transcription: Transcription = JSON.parse(data)
        if (streamReferences.includes(transcription.location)) {
            getStream(transcription.location).instance.processTranscription(transcription)
        }
    });
    socket.on('join', (data: string) => {
        if (streamReferences.includes(data)) {
            socket.join(data);
        }
    });
    socket.on('leave', (data: string) => {
        if (streamReferences.includes(data)) {
            socket.leave(data);
        }
    });
    socket.on('server', (server_state: ServerState) => {
        if (socket.user != "authenticated") return
        if (streamReferences.includes(server_state.location)) {
            updateStatus(server_state)
        }
    });
    socket.on('heartbeat', (heartbeat: Heartbeat) => {
        if (streamReferences.includes(heartbeat.location)) {
            heartBeat(heartbeat)
        }
    });

});

httpServer.listen(process.env.PORT || 3000);

export { io };
