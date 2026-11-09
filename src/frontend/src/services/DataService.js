// 前端调后端的唯一出口：页面只调这里的函数，不直接写 URL。
const BASE_API_URL = process.env.NEXT_PUBLIC_BASE_API_URL;
const REGION = 'cambridge';

async function request(path, options) {
    const response = await fetch(BASE_API_URL + path, options);
    if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
    return response.json();
}

const DataService = {
    // 区域的格子清单（不调 Gemini）：打开页面时先画出整片区域
    Region: () => request(`/regions/${REGION}`),
    // 完整流程：一句话 → 偏好权重 → 每个六边形格子的总分
    Score: (text) => request('/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, region_id: REGION }),
    }),
};

export default DataService;
