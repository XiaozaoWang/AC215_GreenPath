import { readNdjson } from '@/lib/ndjson.mjs';

// Browser requests stay on the frontend origin; Next.js authenticates to the private API.
const BASE_API_URL = '/api';
const REGION = 'city_ma_boston_big';

async function request(path, body) {
    const response = await fetch(BASE_API_URL + path, body === undefined ? undefined : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
    return response.json();
}

// POST JSON and return the response; a failure becomes an Error carrying the API's message in `message` and the HTTP code in `status`
async function post(path, body, signal) {
    const response = await fetch(BASE_API_URL + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    });
    if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const error = new Error(typeof detail.detail === 'string' ? detail.detail : `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return response;
}

const DataService = {
    RagChat: async (question) => {
        const response = await fetch('/api/rag/chat', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The assistant could not answer. Please try again.');
        return result;
    },
    // Route calculation stays in the backend; the map only displays returned results.
    Route: (body) => request('/route', body),
    // The shortest route and the cells that will be predicted, instantly (no text needed)
    RoutePlan: async (body, signal) => (await post('/route/plan', body, signal)).json(),
    // Plan routes for a text and report progress: onEvent(event) is called for every event of the stream
    // (start, preference, cells, result); rejects if the stream reports an error.
    RouteStream: async (body, onEvent, signal) => {
        const response = await post('/route/stream', body, signal);
        for await (const event of readNdjson(response.body)) {
            if (event.type === 'error') throw new Error(event.detail);
            onEvent(event);
        }
    },
    Region: () => request(`/regions/${REGION}`),
    Score: (text) => request('/score', { text, region_id: REGION }),
};

export default DataService;
