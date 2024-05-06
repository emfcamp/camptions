interface LocationType {
    location: string;
    name: string;
}

let locations: Array<LocationType> = [
    {
        location: "stage-a",
        name: "Stage A",
    },
    {
        location: "stage-b",
        name: "Stage B",
    },
    {
        location: "stage-c",
        name: "Stage C",
    },
];

export {
    locations,
    LocationType,
};
