#!/usr/bin/env python3
"""
Rabat-Salé-Témara Bus Explorer Web Server & REST API
Zero-dependency transit server providing both the interactive OpenStreetMap web demo
and machine-readable REST API endpoints for data science & AI agent development.
"""

import os
import sys
import json
import math
import mimetypes
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
DATA_DIR = os.path.join(BASE_DIR, "data")

# Preload dataset into memory for ultra-fast API responses
ROUTES_GEOJSON_PATH = os.path.join(DATA_DIR, "rabat_bus_routes.geojson")
STOPS_GEOJSON_PATH = os.path.join(DATA_DIR, "rabat_bus_stops.geojson")
SUMMARY_JSON_PATH = os.path.join(DATA_DIR, "network_summary.json")

routes_cache = None
stops_cache = None
summary_cache = None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def load_caches():
    global routes_cache, stops_cache, summary_cache
    if os.path.exists(ROUTES_GEOJSON_PATH):
        with open(ROUTES_GEOJSON_PATH, "r", encoding="utf-8") as f:
            routes_cache = json.load(f)
    if os.path.exists(STOPS_GEOJSON_PATH):
        with open(STOPS_GEOJSON_PATH, "r", encoding="utf-8") as f:
            stops_cache = json.load(f)
    if os.path.exists(SUMMARY_JSON_PATH):
        with open(SUMMARY_JSON_PATH, "r", encoding="utf-8") as f:
            summary_cache = json.load(f)

load_caches()

class RabatBusHandler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def resolve_static_path(self, path):
        if path == "/" or path == "/index.html":
            return os.path.join(WEB_DIR, "index.html"), "text/html"
        elif path in ("/style.css", "/app.js"):
            filename = os.path.basename(path)
            mime = "text/css" if filename.endswith(".css") else "application/javascript"
            return os.path.join(WEB_DIR, filename), mime
        elif path.startswith("/data/"):
            filename = os.path.basename(path)
            filepath = os.path.join(DATA_DIR, filename)
            mime, _ = mimetypes.guess_type(filepath)
            return filepath, mime or "application/octet-stream"
        return None, None

    def send_error_headers(self, status):
        self.send_response(status)
        self.send_cors_headers()
        self.end_headers()

    def send_json_headers(self, content_length=None):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        # RFC 7231: HEAD returns identical headers to GET, but MUST NOT return a message body
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/network" or path == "/api/summary":
            if not summary_cache:
                self.send_error_headers(500)
            else:
                self.send_json_headers()
        elif path == "/api/lines":
            if not routes_cache:
                self.send_error_headers(500)
            else:
                self.send_json_headers()
        elif path.startswith("/api/lines/"):
            line_ref = path[len("/api/lines/"):].strip()
            if not routes_cache:
                self.send_error_headers(500)
            else:
                matches = [f for f in routes_cache.get("features", []) if f["properties"].get("line_ref") == line_ref]
                if not matches:
                    self.send_error_headers(404)
                else:
                    self.send_json_headers()
        elif path == "/api/stops" or path == "/api/nearest":
            if not stops_cache:
                self.send_error_headers(500)
            else:
                self.send_json_headers()
        else:
            filepath, mime = self.resolve_static_path(path)
            if filepath and os.path.exists(filepath):
                self.serve_file(filepath, mime, head_only=True)
            else:
                self.send_error_headers(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # -------------------------------------------------------------
        # REST API Endpoints (For future AI agents & data analysis)
        # -------------------------------------------------------------
        if path == "/api/network" or path == "/api/summary":
            self.send_json(summary_cache or {"error": "Dataset not yet built"})
            return

        elif path == "/api/lines":
            if not routes_cache:
                self.send_json({"error": "Dataset not loaded"}, status=500)
                return
            # Deduplicate by line_ref
            lines_dict = {}
            for feat in routes_cache.get("features", []):
                p = feat.get("properties", {})
                ref = p.get("line_ref")
                if ref not in lines_dict:
                    lines_dict[ref] = {
                        "line_ref": ref,
                        "zone": p.get("zone"),
                        "color": p.get("color"),
                        "directions": []
                    }
                lines_dict[ref]["directions"].append({
                    "relation_id": p.get("relation_id"),
                    "name": p.get("name"),
                    "origin": p.get("origin"),
                    "destination": p.get("destination"),
                    "length_km": p.get("length_km"),
                    "stops_count": p.get("stops_count")
                })
            sorted_lines = sorted(lines_dict.values(), key=lambda x: (len(x["line_ref"]), x["line_ref"]))
            self.send_json({"count": len(sorted_lines), "lines": sorted_lines})
            return

        elif path.startswith("/api/lines/"):
            line_ref = path[len("/api/lines/"):].strip()
            if not routes_cache:
                self.send_json({"error": "Dataset not loaded"}, status=500)
                return
            matches = [f for f in routes_cache.get("features", []) if f["properties"].get("line_ref") == line_ref]
            if not matches:
                self.send_json({"error": f"Line {line_ref} not found"}, status=404)
                return
            # Return valid GeoJSON FeatureCollection with stop sequences included in properties
            self.send_json({
                "type": "FeatureCollection",
                "line_ref": line_ref,
                "directions_count": len(matches),
                "features": matches
            })
            return

        elif path == "/api/stops":
            if not stops_cache:
                self.send_json({"error": "Dataset not loaded"}, status=500)
                return
            q = query.get("q", [""])[0].lower().strip()
            line_filter = query.get("line", [""])[0].strip()

            results = []
            for feat in stops_cache.get("features", []):
                p = feat["properties"]
                if line_filter and line_filter not in p.get("lines", []):
                    continue
                if q:
                    name = p.get("stop_name", "").lower()
                    name_ar = p.get("stop_name_ar", "").lower()
                    if q not in name and q not in name_ar:
                        continue
                results.append(feat)

            self.send_json({"count": len(results), "stops": results[:200]})
            return

        elif path == "/api/nearest":
            try:
                lat = float(query.get("lat", [0])[0])
                lon = float(query.get("lon", [0])[0])
                limit = int(query.get("k", [5])[0])
            except ValueError:
                self.send_json({"error": "Invalid lat/lon/k parameters"}, status=400)
                return

            if not stops_cache:
                self.send_json({"error": "Dataset not loaded"}, status=500)
                return

            scored = []
            for feat in stops_cache.get("features", []):
                s_lon, s_lat = feat["geometry"]["coordinates"]
                dist_m = haversine(lat, lon, s_lat, s_lon)
                scored.append({
                    "stop_id": feat["properties"]["stop_id"],
                    "stop_name": feat["properties"]["stop_name"],
                    "stop_name_ar": feat["properties"].get("stop_name_ar", ""),
                    "lat": s_lat,
                    "lon": s_lon,
                    "lines": feat["properties"]["lines"],
                    "distance_meters": round(dist_m, 1)
                })

            scored.sort(key=lambda x: x["distance_meters"])
            self.send_json({
                "origin": {"lat": lat, "lon": lon},
                "nearest_stops": scored[:limit]
            })
            return

        # -------------------------------------------------------------
        # Static File Serving (Web UI & Data Files)
        # -------------------------------------------------------------
        if path == "/" or path == "/index.html":
            self.serve_file(os.path.join(WEB_DIR, "index.html"), "text/html")
        elif path in ("/style.css", "/app.js"):
            filename = os.path.basename(path)
            mime = "text/css" if filename.endswith(".css") else "application/javascript"
            self.serve_file(os.path.join(WEB_DIR, filename), mime)
        elif path.startswith("/data/"):
            filename = os.path.basename(path)
            filepath = os.path.join(DATA_DIR, filename)
            mime, _ = mimetypes.guess_type(filepath)
            self.serve_file(filepath, mime or "application/octet-stream")
        else:
            self.send_error(404, "Not Found")

    def serve_file(self, filepath, content_type, head_only=False):
        if not os.path.exists(filepath):
            self.send_error(404, "File not found")
            return
        try:
            file_size = os.path.getsize(filepath)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_cors_headers()
            self.end_headers()
            if not head_only:
                with open(filepath, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
        except Exception as e:
            if not head_only:
                self.send_error(500, f"Error reading file: {e}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quiet logger for clean CLI output
        return

def run():
    server_address = ("0.0.0.0", PORT)
    httpd = HTTPServer(server_address, RabatBusHandler)
    print(f"================================================================")
    print(f"  Rabat-Salé-Témara Bus Explorer Web Server & REST API")
    print(f"  Listening on: http://localhost:{PORT}")
    print(f"  REST API Endpoints:")
    print(f"    - Network Summary : http://localhost:{PORT}/api/network")
    print(f"    - Bus Lines       : http://localhost:{PORT}/api/lines")
    print(f"    - Nearest Radar   : http://localhost:{PORT}/api/nearest?lat=34.02&lon=-6.83")
    print(f"================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()

if __name__ == "__main__":
    run()
