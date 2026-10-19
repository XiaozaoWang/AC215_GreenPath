// The only way from the frontend to the backend: pages call these functions and never write URLs directly.
const BASE_API_URL = process.env.NEXT_PUBLIC_BASE_API_URL;

async function request(path, options) {
    const response = await fetch(BASE_API_URL + path, options);
    if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
    return response.json();
}

const DataService = {
    // Saved predictions of the default result version, without asking the LLM (used to draw the map on load)
    Preview: () => request("/preview"),
    // Full flow: one sentence → preference weights → a score for every predicted cell
    Score: (text) => request("/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    }),
};

export default DataService;
