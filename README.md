# 🇲🇦 Morocco Transit Map (خريطة النقل الحضري بالمغرب)

An open-source transit data pipeline and interactive OpenStreetMap web application mapping public transit across Morocco.

🚀 **Live Interactive Demo:** [https://walsoup.github.io/morocco-transit-map/](https://walsoup.github.io/morocco-transit-map/)

Currently covers the **Rabat–Salé–Kénitra / Rabat–Salé–Témara** metropolitan area (ALSA Citybus & STRS Tramway T1/T2), expanding to other Moroccan transit regions and national rail/trains (ONCF) soon!

---

## 🌐 Live Demo & Web App

You can access the live map directly in your browser:
👉 **[walsoup.github.io/morocco-transit-map](https://walsoup.github.io/morocco-transit-map/)**

Fully responsive for desktop, tablet, and mobile with GPS nearest-stop radar and full offline-first data exports.

---

## 🗺️ Coverage & Roadmap
- [x] **Rabat–Salé–Témara–Skhirat**: ALSA Citybus lines + STRS Tramway T1 & T2
- [ ] **Kénitra & North Extension**: Urban & suburban transit lines
- [ ] **Casablanca-Settat**: Casa Tramway (T1–T4), Busway, and Alsa Al Baida
- [ ] **Marrakech-Safi**: ALSA Marrakech & Electric BRT
- [ ] **Trains / Rail (ONCF)**: Al Boraq (LGV), Al Atlas, TNR lines (coming soon)

---

## 📊 1. The "Data Scientist" Approach: Transit Data Modeling

In public transit data science, when transit operators (ALSA and STRS) partner directly with Google Transit without publishing an open downloadable GTFS `.zip` on their corporate portal, the **industry-standard open methodology** is to leverage **OpenStreetMap (OSM) Public Transport v2 Relations**.

### Data Architecture & Pipeline (`etl_bus_data.py`)
1. **Catalog Ingestion**: Extracts all active ALSA Citybus lines and STRS Tramway lines cataloged and maintained by the OSM transit community for Rabat–Salé–Témara.
2. **OSM Overpass / API Extraction**: Retrieves full relation trees containing `type=route`, `route=bus` / `route=tram`, `network=ALSA Citybus RST` & `network=STRS`, including all member ways (driving/rail geometries) and nodes (`public_transport=platform` / `bus_stop` / `tram_stop`).
3. **Topological Polyline Reconstruction**: Stitches street and rail segments into continuous polylines, handling roundabouts, reversible ways, and one-way loops.
4. **Target / Stop Harmonization**: Indexes **1,191 unique transit targets (bus stops & tram stations)** with bilingual naming (Latin/Arabic), geo-coordinates, transit modes (`bus`, `tram`), amenity flags, and an inverted index of passing lines.
5. **Standardized Exports**:
   - `data/rabat_bus_routes.geojson`: Full vector route paths with metadata (distance, mode, zones, stops count).
   - `data/rabat_bus_stops.geojson`: All bus & tram stops with interconnected lines.
   - `data/routes.csv`: GTFS-style routes table.
   - `data/stops.csv`: GTFS-style stops & stations table.
   - `data/route_stops.csv`: Ordered sequence of stops per line & direction.
   - `data/network_summary.json`: High-level network topology & hub connectivity matrix.

### Key Network Figures
| Metric | Value |
| :--- | :--- |
| **Active Transit Lines** | **44 lines** (42 Bus lines + **Tramway T1 & T2**) [Catalog: 45 lines] |
| **Total Route Directions** | **87 directions** (Aller / Retour) |
| **Total Unique Targets (Stops & Stations)** | **1,189 targets** |
| **Total Network Length** | **1,620+ km** |
| **Tramway Lines** | **T1** (Madinat Al Irfane ↔ Hay Karima, 19.5 km) & **T2** (Yacoub El Mansour ↔ Hôpital Moulay Abdellah, 17 km) |
| **Top Transit Hubs** | **Abdelmoumen** (13 lines), **Jour et Nuit** (12), **Bab Mellah** (11 + Tram T1/T2), **Théâtre Mohammed V** (11), **Milano** (10), **Bab El Had** (9) |

---

## 🌐 2. Interactive OpenStreetMap Web Demo

An interactive, responsive single-page web application powered by **Leaflet.js** and **OpenStreetMap**:

### Quick Start
Start the server (starts the web demo and the REST API):
```bash
python3 server.py
```
Open **[http://localhost:8080](http://localhost:8080)** in your browser.

### Features
- **Route Visualizer**: Color-coded bus polylines across Rabat, Salé, Témara, Harhoura, Skhirat, Tamesna, Bouknadel, and Ain El Aouda.
- **Bus Targets (Stops)**: Every stop is marked on the map. Clicking any stop displays its name (French + Arabic), coordinates, and interactive badges of every bus line that passes through it.
- **Search & Filtering**: Search instantly by line number (e.g. `32H`, `1`, `AE`, `101`) or destination (`Harhoura`, `Bab El Had`, `Technopolis`, `Agdal`).
- **Zone Filters**: Quick tabs for *Rabat Urban Core*, *Salé & North*, *Témara & South*, *Intercommunal*, and *Airport Express*.
- **Itinerary Timeline**: Select any line to view its stop-by-stop sequence, switch direction (Aller vs Retour), and click any stop to fly straight to it.
- **Nearest Stop Radar**: Click anywhere on the map (or use "My GPS Location") to instantly calculate the 5 closest bus stops and find out which buses you can catch!
- **Multi-Basemap Toggle**: Switch between *CARTO Positron* (clean minimal), *CARTO Dark Matter*, *OpenStreetMap Standard*, and *Satellite Imagery*.
- **Data Export & Analytics Modal**: Summary charts and direct download links for GeoJSON and CSV datasets.

---

## 🤖 3. Future Agent & Developer REST API

The included `server.py` provides high-speed, CORS-enabled REST API endpoints designed to power your upcoming AI agent:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/network` | `GET` | High-level network summary, line count, total km, and top transfer hubs. |
| `/api/lines` | `GET` | List all 43 lines with their endpoints, zones, and distances. |
| `/api/lines/<ref>` | `GET` | Full GeoJSON and stop sequences for a specific line (e.g. `/api/lines/32H`). |
| `/api/stops?q=agdal` | `GET` | Search bus stops by name or filter with `?line=32H`. |
| `/api/nearest?lat=34.02&lon=-6.83&k=5` | `GET` | Spatial query returning the closest bus stops & reachable lines for an agent's pathfinding. |
| `/data/<filename>` | `GET` | Raw access to `.geojson` and `.csv` files. |

---

## 📁 Repository Structure
```
/home/wal/work/MAP/
├── data/
│   ├── rabat_bus_routes.geojson    # Full line geometries (84 features)
│   ├── rabat_bus_stops.geojson     # 1,131 geolocated bus targets
│   ├── routes.csv                  # Tabular routes (GTFS style)
│   ├── stops.csv                   # Tabular stops (GTFS style)
│   ├── route_stops.csv             # Sequence schedule table
│   └── network_summary.json        # Analytics & metadata
├── web/
│   ├── index.html                  # Responsive OpenStreetMap web UI
│   ├── style.css                   # Custom transit map styling & animations
│   └── app.js                      # Leaflet map logic, filters, timeline & radar
├── server.py                       # Zero-dependency Web & REST API server
├── etl_bus_data.py                 # Reproducible OSM ETL extraction pipeline
└── README.md                       # Documentation
```
