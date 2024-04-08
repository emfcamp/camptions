const streamTextConfig = {
	baseUrl: "https://www.streamtext.net/text-data.ashx",
	event: "IHaveADream",
	language: "en",
	password: "testingPass"
}

async function pollStreamtext(lastIndex, lastData) {
	console.log("Polling");
	const response = await fetch(`${streamTextConfig.baseUrl}?event=${streamTextConfig.event}&language=${streamTextConfig.language}&last=${lastIndex}`, {
		"headers": {
			"accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
			// "authorization": `Basic ${btoa(streamTextConfig.password)}`,
		},
		"method": "GET"
	});

	if (!response.ok) {
		throw new Error(`HTTP error! status: ${response.status}`);
	}

	const data = await response.json();
	console.log(data);

	if (data.i.length == 0) { captions = "" } else { captions = decodeURIComponent(data.i[0].d) }

	return {
		last: data.lastPosition,
		captions
	};
};

function appendCaptions(captions) {
	console.log("Appending captions");
	const captionsElement = document.getElementById('captions');
	captionsElement.innerHTML = captionsElement.innerHTML + captions;
};

function onLoad() {
	let lastIndex = -1;
	let lastData = "";

	document.getElementById("captions").innerText = "";

	setInterval(async () => {
		const { last: newLast, captions } = await pollStreamtext(lastIndex, lastData);

		appendCaptions(captions);

		lastIndex = newLast;
		lastData = captions;
	}, 1000);
}

document.addEventListener('DOMContentLoaded', onLoad);