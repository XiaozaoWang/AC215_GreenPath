// The only way out from the frontend to the backend: pages only call the functions here and never write URLs directly.
import { BASE_API_URL } from "./Common";
import axios from 'axios';

const DataService = {
    Capabilities: async function () { return await axios.get(BASE_API_URL + "/capabilities"); },
    // Health check: is the backend alive
    Health: async function () {
        return await axios.get(BASE_API_URL + "/", { timeout: 3000 });
    },
    // Full flow ①–⑩: one sentence + region → heatmap + the intermediate results of every step
    Score: async function (text, regionId = null) {
        return await axios.post(BASE_API_URL + "/score", { text: text, region_id: regionId });
    },
    // Only run the harness: one sentence → weights + Q&A record
    Preference: async function (text) {
        return await axios.post(BASE_API_URL + "/preference", { text: text });
    },
    // Backend code viewer: which files can be viewed
    SourceList: async function () {
        return await axios.get(BASE_API_URL + "/source");
    },
    // Backend code viewer: the source code of one file
    Source: async function (path) {
        return await axios.get(BASE_API_URL + "/source/" + path);
    },
}

export default DataService;
