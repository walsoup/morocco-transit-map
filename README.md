# Morocco Transit Map

An interactive map of bus and tram lines in the Rabat-Salé-Témara area, plus the script and exported data behind it.

**Live demo:** https://walsoup.github.io/morocco-transit-map/

The data comes from OpenStreetMap public transport relations. It is not an official ALSA or STRS dataset, so routes and stops can differ from what actually runs.

## Coverage

| | |
| :-- | :-- |
| Lines | 44 (42 bus, tram T1 and T2) |
| Route directions | 87 |
| Stops and stations | 1,189 |
| Total route length | about 1,620 km (sum of all directions) |

Line 21 is listed in the catalog but isn't included in the current route export.

Planned, not built: Kénitra, Casablanca-Settat, Marrakech-Safi and ONCF rail.

## Features

- Color-coded line geometries on a Leaflet map
- Stop popups with French and Arabic names and the lines serving each stop
- Search by line number or destination
- Zone filters: Rabat Urban Core, Salé & North, Témara & South, Intercommunal, Airport Express, Tramway
- Stop-by-stop timeline per line, with direction switch and fly-to on click
- Nearest-stop finder: click the map or use your GPS location to list the 5 closest stops and their lines
- Basemap switch: CARTO Positron, CARTO Dark Matter, OpenStreetMap, Esri satellite
- Data modal with GeoJSON and CSV downloads

## Run locally

Requires Python 3. No packages to install.

```bash
python3 server.py
```

Then open http://localhost:8080. Set `PORT` to use another port.

## API

`server.py` also serves a small read-only JSON API with CORS enabled.

| Endpoint | Returns |
| :-- | :-- |
| `/api/network` | Network summary and top transfer hubs |
| `/api/lines` | All lines with endpoints, zones and distances |
| `/api/lines/<ref>` | GeoJSON and stop sequence for one line, e.g. `/api/lines/32H` |
| `/api/stops?q=agdal` | Stop search by name; add `line=32H` to filter by line |
| `/api/nearest?lat=34.02&lon=-6.83&k=5` | Closest stops and the lines that reach them |
| `/data/<filename>` | Raw `.geojson` and `.csv` files |

## Data

Files in `data/`:

| File | Contents |
| :-- | :-- |
| `rabat_bus_routes.geojson` | Route geometry and metadata, one feature per direction |
| `rabat_bus_stops.geojson` | Stops and stations with the lines serving them |
| `routes.csv` | Routes table, GTFS-style |
| `stops.csv` | Stops table, GTFS-style |
| `route_stops.csv` | Ordered stops per line and direction |
| `network_summary.json` | Totals, hubs and line catalog |

To rebuild them from OSM:

```bash
python3 etl_bus_data.py
```

The script pulls route relations from OSM, joins the way segments into polylines, merges stops, assigns zones and writes the files above. It needs internet access.

## Repository layout

```
data/              exported datasets
web/               front end (index.html, style.css, app.js)
server.py          static server and API
etl_bus_data.py    OSM extraction script
.github/workflows/ GitHub Pages deploy
```

The Pages workflow publishes `web/` and `data/` on every push to `main`.

## Credits

- Transit data: © OpenStreetMap contributors (ODbL)
- Basemaps: © OpenStreetMap contributors, © CARTO; satellite tiles © Esri
- Map library: Leaflet
