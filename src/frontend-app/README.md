# Frontend

Next.js/React and Leaflet provide a phone-style interface at http://localhost:3000.
The user enters a riding preference; the app asks the API for scores and recolors H3 cells.
Colored cells have saved predictions; gray cells have no prediction. Routing is not implemented.

## Run

From the repository root, after copying `.env.example` to `.env`:

```sh
docker compose up --build -d --wait
```

This starts both the API and frontend. Enter `I want a quiet ride with open views`.
The default parser works offline with fixed keywords. To use Gemini, follow the
[API guide](../api-service/README.md#using-the-real-gemini). Stop with `docker compose down`.
The satellite basemap requires internet access.

## Main files

| File under `src/` | Responsibility |
| --- | --- |
| `app/page.jsx` | Phone layout, preference input and request state. |
| `components/HexMap.jsx` | Leaflet basemap, H3 geometry and score colors. |
| `components/colors.js` | Color mapping. |
| `services/DataService.js` | `/preview` on page load and `/score` on submission. |

`NEXT_PUBLIC_BASE_API_URL` points the browser to the API (default http://localhost:9000).
Compose installs Node dependencies with `npm ci`. No vision model runs in the browser;
the supported result bundle connects the app to independently produced batch predictions.

[App example](../../docs/app/assets/frontend-example.png) ·
[Concept diagram](../../docs/app/assets/preference-scoring-flow.png) ·
[Design notes](../../docs/APP_DESIGN.md)
