// Read a newline-delimited JSON stream (one JSON object per line) and yield each object as soon as its line is complete.
export async function* readNdjson(body) {
    const decoder = new TextDecoder();
    let buffer = '';
    for await (const chunk of body) {
        buffer += decoder.decode(chunk, { stream: true });
        let end;
        while ((end = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, end).trim();
            buffer = buffer.slice(end + 1);
            if (line) yield JSON.parse(line);
        }
    }
    if (buffer.trim()) yield JSON.parse(buffer);
}
