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
disconnected: client timed out
client-only: client but not server
connected: client and server connected
`

let locations: Locations = {
    "stage-a": {
        location: "stage-a",
        name: "Stage A",
        status: "disconnected",
    },
    "stage-b": {
        location: "stage-b",
        name: "Stage B",
        status: "disconnected",
    },
    "stage-c": {
        location: "stage-c",
        name: "Stage C",
        status: "disconnected",
    },
};

let heartbeats: LastHeartbeat = {}

function updateStatus(data: ServerState) {
    if (data.status == "connected") {
        locations[data.location]["status"] = "connected"
    } else if (data.status == "disconnected") {
        locations[data.location]["status"] = "client-only"
    }
    io.to(data.location).emit("location", locations[data.location])
}

function heartBeat(data: Heartbeat) {
    heartbeats[data.location] = new Date()
}

function checkHeartbeats() {
    for (const [location, time] of Object.entries(heartbeats)) {
        if (time.getTime() + (1000 * 15) < new Date().getTime()) {
            locations[location]["status"] = "disconnected"
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
