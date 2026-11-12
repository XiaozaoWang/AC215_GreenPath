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
    Region: () => request(`/regions/${REGION}`),
    Score: (text) => request('/score', { text, region_id: REGION }),
};

export default DataService;
