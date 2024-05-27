import { io } from "./index";

interface LocationType {
    location: string;
    name: string;
    status: string;
}

interface Locations {
    [location: string]: LocationType
}

interface LastHeartbeat {
    [location: string]: Date
}

interface ServerState {
    location: string;
    status: string;
}

interface Heartbeat {
    location: string;
}

` status states:
no-streaming-client: client timed out
no-transcription-server: client but not server
connected: client and server connected
`

let locations: Locations = {
    "stage-a": {
        location: "stage-a",
        name: "Stage A",
        status: "no-streaming-client",
    },
    "stage-b": {
        location: "stage-b",
        name: "Stage B",
        status: "no-streaming-client",
    },
    "stage-c": {
        location: "stage-c",
        name: "Stage C",
        status: "no-streaming-client",
    },
};

let heartbeats: LastHeartbeat = {}

function updateStatus(data: ServerState) {
    if (data.status == "connected") {
        locations[data.location]["status"] = "connected"
    } else if (data.status == "disconnected") {
        locations[data.location]["status"] = "no-transcription-server"
    }
    io.to(data.location).emit("location", locations[data.location])
}

function heartBeat(data: Heartbeat) {
    heartbeats[data.location] = new Date()
    if (locations[data.location]["status"] == "no-streaming-client") {
        locations[data.location]["status"] = "connected"
        io.to(data.location).emit("location", locations[data.location])
    }
}

function checkHeartbeats() {
    for (const [location, time] of Object.entries(heartbeats)) {
        if (time.getTime() + (1000 * 15) < new Date().getTime()) {
            locations[location]["status"] = "no-streaming-client"
            io.to(location).emit("location", locations[location])
        }
    }
}

setInterval(function () {
    checkHeartbeats()
}, 1000);

export {
    locations,
    LocationType,
    ServerState,
    Heartbeat,
    updateStatus,
    heartBeat,
};
