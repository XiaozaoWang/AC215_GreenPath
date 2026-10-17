//export const BASE_API_URL = process.env.REACT_APP_BASE_API_URL;
export const BASE_API_URL = process.env.NEXT_PUBLIC_BASE_API_URL;
export const APP_VERSION = 1.0;

export function epochToJsDate(ts) {
    let dt = new Date(ts)
    return dt.toLocaleDateString() + " " + dt.toLocaleTimeString();
}

export function uuid() {
    const newUuid = ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, (c) =>
        (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16),
    )
    return newUuid;
}