import express, { Request, Response } from 'express';
import fetch from 'node-fetch';
const app = express();

interface StreamTextConfig {
	baseUrl: string;
	event: string;
	language: string;
	password: string;
}

interface StreamTextData {
	lastPosition: number;
	i: Array<{
		format: string;
		d: string;
	}>;
}

const streamTextConfig: StreamTextConfig = {
	baseUrl: "https://www.streamtext.net/text-data.ashx",
	event: "IHaveADream",
	language: "en",
	password: "testingPass"
};

async function pollStreamtext(lastIndex: string, lastData: string) {
	console.log("Polling");
	console.log(lastIndex);
	console.log(`${streamTextConfig.baseUrl}?event=${streamTextConfig.event}&language=${streamTextConfig.language}&last=${lastIndex}`)
	const response = await fetch(`${streamTextConfig.baseUrl}?event=${streamTextConfig.event}&language=${streamTextConfig.language}&last=${lastIndex}`, {
		"headers": {
			"accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
			"authorization": `Basic ${Buffer.from(streamTextConfig.password).toString('base64')}`,
		},
		"method": "GET"
	});

	if (!response.ok) {
		throw new Error(`HTTP error! status: ${response.status}`);
	}

	const data = await response.json() as StreamTextData;

	console.log(data);

	let captions: string = "";
	if (data.i.length !== 0) { captions = decodeURIComponent(data.i[0].d) }

	return {
		last: data.lastPosition,
		captions
	};
};

app.get('/poll', async (req: Request, res: Response) => {
	try {
		let lastIndex = "-1";
		if (req.query.lastIndex != undefined) { lastIndex = req.query.lastIndex as string; }
		const result = await pollStreamtext(lastIndex, req.query.lastData as string);
		res.json(result);
	} catch (error: any) {
		res.status(500).send(error.message);
	}
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server started on port ${PORT}`));