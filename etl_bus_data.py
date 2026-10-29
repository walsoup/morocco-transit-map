#!/usr/bin/env python3
"""
Rabat-Salé-Témara Bus Network ETL Pipeline
Extracts, harmonizes, and formats all ALSA Citybus RST bus routes and stops from OpenStreetMap.
"""

import os
import re
import json
import time
import math
import csv
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
os.makedirs(RAW_DIR, exist_ok=True)

USER_AGENT = "RabatDataScienceTransit/1.0 (academic research & open transit mapping)"

# Vibrant, accessible color palette for Rabat bus lines
LINE_COLORS = [
    "#E63946", "#1D3557", "#457B9D", "#2A9D8F", "#E76F51",
    "#F4A261", "#8338EC", "#3A86FF", "#FB5607", "#06D6A0",
    "#118AB2", "#073B4C", "#D90429", "#7209B7", "#3F37C9",
    "#4361EE", "#4895EF", "#4CC9F0", "#588157", "#3A5A40",
    "#606C38", "#283618", "#BC6C25", "#DDA15E", "#9B5DE5",
    "#F15BB5", "#00BBF9", "#00F5D4", "#9D0208", "#6A040F"
]

def haversine_distance(p1, p2):
    """Calculate distance in meters between two [lon, lat] coordinates."""
    lon1, lat1 = p1
    lon2, lat2 = p2
    R = 6371000  # meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def fetch_wiki_relations():
    """Fetches the official catalog of ALSA Rabat-Salé-Témara relations from OpenStreetMap Wiki."""
    print("[1/5] Fetching bus relation catalog from OSM Wiki...")
    url = "https://wiki.openstreetmap.org/w/api.php?action=parse&page=Rabat&prop=wikitext&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        wikitext = data["parse"]["wikitext"]["*"]

    rows = wikitext.split("|-")
    routes = []
    current_ref = None

    for row in rows:
        ref_match = re.search(r"\{\{Sketch Line\|([^|]+)\|", row)
        if ref_match:
            current_ref = ref_match.group(1).strip()
        
        rel_match = re.search(r"\{\{relation\|(\d+)", row)
        if rel_match and current_ref:
            rel_id = int(rel_match.group(1).strip())
            name_match = re.search(r"\|\s*([^|\n{]+(?:–|-)[^|\n{]+)\s*\|\|\s*\{\{relation", row)
            name = name_match.group(1).strip() if name_match else f"Line {current_ref}"
            
            # Check if this is a Tramway line (T1 or T2)
            is_tram = ("Madinat Al Irfane" in name or "Hay Karima" in name or 
                       "Yacoub el Mansour" in name or "Hôpital Moulay Abdellah" in name)
            
            mode = "tram" if is_tram else "bus"
            display_ref = f"T{current_ref}" if is_tram else current_ref

            routes.append({
                "ref": display_ref,
                "raw_ref": current_ref,
                "mode": mode,
                "name": name,
                "relation_id": rel_id
            })

    print(f"  Found {len(routes)} route relations (Buses & Tram T1/T2) across {len(set(r['ref'] for r in routes))} unique lines.")
    return routes

def fetch_relation_data(rel_id, retries=3, backoff_factor=1.0):
    """Fetches full relation JSON from OSM API or local cache with retry/backoff."""
    cache_path = os.path.join(RAW_DIR, f"relation_{rel_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    url = f"https://api.openstreetmap.org/api/0.6/relation/{rel_id}/full.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                content = resp.read().decode("utf-8")
                data = json.loads(content)
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                sleep_sec = backoff_factor * (2 ** (attempt - 1))
                print(f"    Notice: Retry {attempt}/{retries} for relation {rel_id} after {sleep_sec}s: {e}")
                time.sleep(sleep_sec)
        except json.JSONDecodeError as e:
            print(f"    Error: Invalid JSON received for relation {rel_id}: {e}")
            return None
        except Exception as e:
            print(f"    Error: Unexpected failure fetching relation {rel_id}: {e}")
            return None

    print(f"    Warning: Failed to fetch relation {rel_id} after {retries} attempts: {last_err}")
    return None

def stitch_ways_into_coordinates(way_ids, ways_dict, nodes_dict):
    """
    Stitches ordered OSM ways into coordinate polylines.
    Handles reversibility and breaks at true discontinuities.
    """
    segments = []
    current_segment = []

    for w_id in way_ids:
        way = ways_dict.get(w_id)
        if not way:
            continue
        w_nodes = [nodes_dict[n] for n in way.get("nodes", []) if n in nodes_dict]
        if not w_nodes:
            continue
        w_coords = [[round(n["lon"], 6), round(n["lat"], 6)] for n in w_nodes]

        if not current_segment:
            current_segment = w_coords
        else:
            # Check gap to next way
            d_head = haversine_distance(current_segment[-1], w_coords[0])
            d_tail = haversine_distance(current_segment[-1], w_coords[-1])

            # If within 150m, consider connected
            if d_head <= 150:
                current_segment.extend(w_coords[1:])
            elif d_tail <= 150:
                current_segment.extend(list(reversed(w_coords))[:-1])
            else:
                # Gap detected, start new segment
                segments.append(current_segment)
                current_segment = w_coords

    if current_segment:
        segments.append(current_segment)

    return segments

def classify_zone(ref, name):
    """Classifies route into geographic zones: Rabat, Sale, Temara, Coastal, Interurban."""
    n = name.lower()
    r = str(ref).upper()
    # Check Airport Express FIRST so specific routes (like 210AE Agdal-Airport) don't get swallowed by Agdal/Rabat
    if "AE" in r or "aeroport" in n or "aéroport" in n or "airport" in n:
        return "Airport Express"
    elif r.startswith("T"):
        return "Tramway"
    elif r.startswith("20") or "salé" in n or "sale" in n or "karia" in n or "bouknadel" in n:
        return "Salé & North"
    elif r.startswith("30") or "temara" in n or "témara" in n or "harhoura" in n or "skhirat" in n or "ain atik" in n:
        return "Témara, Harhoura & South"
    elif r.startswith("10") or "fath" in n or "ennahda" in n or "agdal" in n or "chellah" in n or "had" in n:
        return "Rabat Urban Core"
    else:
        return "Intercommunal"

def process_network():
    """Main ETL execution."""
    routes_catalog = fetch_wiki_relations()

    print(f"[2/5] Downloading & caching full OSM relations ({len(routes_catalog)} items)...")
    raw_records = []
    for idx, r_meta in enumerate(routes_catalog):
        rel_id = r_meta["relation_id"]
        data = fetch_relation_data(rel_id)
        if data:
            raw_records.append((r_meta, data))
        # Small delay to respect OSM API guidelines
        time.sleep(0.15)
        if (idx + 1) % 15 == 0 or idx == len(routes_catalog) - 1:
            print(f"  Downloaded {idx + 1}/{len(routes_catalog)} relations...")

    print(f"[3/5] Parsing geometries, reconstructing routes, and indexing transit targets...")
    
    unique_lines = sorted(list(set(r["ref"] for r in routes_catalog)), key=lambda x: (len(x), x))
    color_map = {ref: LINE_COLORS[i % len(LINE_COLORS)] for i, ref in enumerate(unique_lines)}
    color_map["T1"] = "#1E40AF"  # Official Cobalt Blue for Tram T1
    color_map["T2"] = "#BE185D"  # Official Magenta/Rose for Tram T2

    processed_routes = []
    unique_stops = {}  # stop_id -> stop dict
    route_stops_tabular = []
    routes_tabular = []

    for r_meta, data in raw_records:
        rel_id = r_meta["relation_id"]
        line_ref = r_meta["ref"]
        mode = r_meta.get("mode", "bus")
        
        elements = data.get("elements", [])
        nodes = {el["id"]: el for el in elements if el["type"] == "node"}
        ways = {el["id"]: el for el in elements if el["type"] == "way"}
        
        # Find relation element
        rel_el = next((el for el in elements if el["type"] == "relation"), None)
        if not rel_el:
            continue

        rel_tags = rel_el.get("tags", {})
        members = rel_el.get("members", [])

        # Extract ordered stops
        stops_in_route = []
        way_refs = []

        for m in members:
            m_type = m.get("type")
            m_role = m.get("role", "")
            m_ref = m.get("ref")

            # Only include explicit transit stops/platforms (skip stray ways/crossings with empty role)
            if m_type == "node" and ("stop" in m_role or "platform" in m_role):
                node = nodes.get(m_ref)
                if node:
                    tags = node.get("tags", {})
                    # Stop name resolution
                    stop_name = (
                        tags.get("name") or
                        tags.get("name:fr") or
                        tags.get("name:ar") or
                        tags.get("ref") or
                        (f"Station {line_ref}" if mode == "tram" else f"Arrêt {line_ref}")
                    )
                    stop_name_ar = tags.get("name:ar", "")
                    
                    stop_info = {
                        "stop_id": m_ref,
                        "stop_name": stop_name,
                        "stop_name_ar": stop_name_ar,
                        "lat": round(node["lat"], 6),
                        "lon": round(node["lon"], 6),
                        "role": m_role or "platform",
                        "sequence": len(stops_in_route) + 1
                    }
                    stops_in_route.append(stop_info)

                    # Update global unique stops index
                    if m_ref not in unique_stops:
                        unique_stops[m_ref] = {
                            "stop_id": m_ref,
                            "stop_name": stop_name,
                            "stop_name_ar": stop_name_ar,
                            "lat": round(node["lat"], 6),
                            "lon": round(node["lon"], 6),
                            "lines": [line_ref],
                            "modes": [mode],
                            "routes_count": 1,
                            "has_shelter": tags.get("shelter") == "yes",
                            "has_bench": tags.get("bench") == "yes"
                        }
                    else:
                        if line_ref not in unique_stops[m_ref]["lines"]:
                            unique_stops[m_ref]["lines"].append(line_ref)
                            unique_stops[m_ref]["routes_count"] += 1
                        if mode not in unique_stops[m_ref].get("modes", []):
                            unique_stops[m_ref].setdefault("modes", []).append(mode)

            elif m_type == "way":
                way_refs.append(m_ref)

        # Stitch ways into polylines
        segments = stitch_ways_into_coordinates(way_refs, ways, nodes)
        
        # Calculate route length
        total_length_m = 0
        for seg in segments:
            for i in range(len(seg) - 1):
                total_length_m += haversine_distance(seg[i], seg[i+1])
        length_km = round(total_length_m / 1000.0, 2)

        # Route naming and endpoints
        official_name = rel_tags.get("name") or r_meta["name"]
        origin = rel_tags.get("from")
        destination = rel_tags.get("to")
        if not origin or not destination:
            parts = official_name.split("–") if "–" in official_name else official_name.split("-")
            if len(parts) >= 2:
                origin = origin or parts[0].strip()
                destination = destination or parts[1].strip()
            else:
                origin = origin or "Départ"
                destination = destination or "Terminus"

        zone = classify_zone(line_ref, official_name)
        default_op = "STRS / Transdev" if mode == "tram" else "ALSA"
        default_net = "Tramway de Rabat-Salé" if mode == "tram" else "ALSA Citybus RST"

        route_record = {
            "relation_id": rel_id,
            "line_ref": line_ref,
            "mode": mode,
            "name": official_name,
            "origin": origin,
            "destination": destination,
            "operator": rel_tags.get("operator", default_op),
            "network": rel_tags.get("network", default_net),
            "zone": zone,
            "color": color_map.get(line_ref, "#E63946"),
            "length_km": length_km,
            "stops_count": len(stops_in_route),
            "ways_count": len(way_refs),
            "segments": segments,
            "stops": stops_in_route
        }
        processed_routes.append(route_record)

        # For tabular CSV
        routes_tabular.append({
            "route_id": rel_id,
            "line_ref": line_ref,
            "mode": mode,
            "route_name": official_name,
            "origin": origin,
            "destination": destination,
            "zone": zone,
            "length_km": length_km,
            "stops_count": len(stops_in_route),
            "color": color_map.get(line_ref, "#E63946")
        })

        for s in stops_in_route:
            route_stops_tabular.append({
                "route_id": rel_id,
                "line_ref": line_ref,
                "stop_sequence": s["sequence"],
                "stop_id": s["stop_id"],
                "stop_name": s["stop_name"],
                "stop_name_ar": s["stop_name_ar"],
                "lat": s["lat"],
                "lon": s["lon"]
            })

    # Calculate lines from actually processed/exported routes
    exported_lines = sorted(list(set(r["line_ref"] for r in processed_routes)), key=lambda x: (len(x), x))
    catalog_lines = unique_lines

    print(f"  Processed {len(processed_routes)} routes across {len(exported_lines)} active lines (catalog: {len(catalog_lines)} lines) and {len(unique_stops)} unique bus stops.")

    print(f"[4/5] Generating standard GeoJSON & GTFS-style outputs...")

    # 1. GeoJSON for Routes
    routes_geojson = {
        "type": "FeatureCollection",
        "name": "Rabat_Sale_Temara_Bus_Routes",
        "features": []
    }
    for r in processed_routes:
        geom_type = "MultiLineString"
        coords = r["segments"]
        if not coords:
            continue
        feat = {
            "type": "Feature",
            "properties": {
                "relation_id": r["relation_id"],
                "line_ref": r["line_ref"],
                "mode": r["mode"],
                "name": r["name"],
                "origin": r["origin"],
                "destination": r["destination"],
                "operator": r["operator"],
                "network": r["network"],
                "zone": r["zone"],
                "color": r["color"],
                "length_km": r["length_km"],
                "stops_count": r["stops_count"],
                "stops": [{"id": s["stop_id"], "name": s["stop_name"], "seq": s["sequence"]} for s in r["stops"]]
            },
            "geometry": {
                "type": geom_type,
                "coordinates": coords
            }
        }
        routes_geojson["features"].append(feat)

    # 2. GeoJSON for Bus & Tram Stops / Targets
    stops_geojson = {
        "type": "FeatureCollection",
        "name": "Rabat_Sale_Temara_Transit_Stops",
        "features": []
    }
    for stop_id, s in unique_stops.items():
        feat = {
            "type": "Feature",
            "properties": {
                "stop_id": s["stop_id"],
                "stop_name": s["stop_name"],
                "stop_name_ar": s["stop_name_ar"],
                "lines": sorted(s["lines"], key=lambda x: (len(x), x)),
                "lines_str": ", ".join(sorted(s["lines"], key=lambda x: (len(x), x))),
                "modes": s.get("modes", ["bus"]),
                "is_tram": "tram" in s.get("modes", []),
                "routes_count": s["routes_count"],
                "has_shelter": s["has_shelter"],
                "has_bench": s["has_bench"]
            },
            "geometry": {
                "type": "Point",
                "coordinates": [s["lon"], s["lat"]]
            }
        }
        stops_geojson["features"].append(feat)

    # Write GeoJSON files
    with open(os.path.join(DATA_DIR, "rabat_bus_routes.geojson"), "w", encoding="utf-8") as f:
        json.dump(routes_geojson, f, ensure_ascii=False, indent=2)

    with open(os.path.join(DATA_DIR, "rabat_bus_stops.geojson"), "w", encoding="utf-8") as f:
        json.dump(stops_geojson, f, ensure_ascii=False, indent=2)

    # 3. Unified Compact JSON for Web Demo & APIs
    network_summary = {
        "metadata": {
            "network_name": "Rabat-Salé-Témara Public Transit Network (ALSA Citybus & STRS Tramway)",
            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_lines": len(exported_lines),
            "catalog_total_lines": len(catalog_lines),
            "total_routes_directions": len(processed_routes),
            "total_unique_stops": len(unique_stops),
            "total_network_km": round(sum(r["length_km"] for r in processed_routes), 1),
            "tram_lines": ["T1", "T2"],
            "bus_lines": [l for l in exported_lines if not l.startswith("T")],
            "zones": list(set(r["zone"] for r in processed_routes)),
            "top_hubs": sorted(
                [{"stop_id": s["stop_id"], "name": s["stop_name"], "lines": s["lines"], "count": len(s["lines"])}
                 for s in unique_stops.values()],
                key=lambda x: x["count"],
                reverse=True
            )[:15]
        },
        "lines": exported_lines,
        "catalog_lines": catalog_lines,
        "color_map": color_map
    }

    with open(os.path.join(DATA_DIR, "network_summary.json"), "w", encoding="utf-8") as f:
        json.dump(network_summary, f, ensure_ascii=False, indent=2)

    # 4. Standard CSV files (Data Science / GTFS table style)
    with open(os.path.join(DATA_DIR, "routes.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["route_id", "line_ref", "mode", "route_name", "origin", "destination", "zone", "length_km", "stops_count", "color"])
        writer.writeheader()
        writer.writerows(routes_tabular)

    with open(os.path.join(DATA_DIR, "stops.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["stop_id", "stop_name", "stop_name_ar", "lat", "lon", "lines", "modes", "routes_count", "has_shelter", "has_bench"])
        writer.writeheader()
        for s in unique_stops.values():
            row = dict(s)
            row["lines"] = ";".join(s["lines"])
            row["modes"] = ";".join(s.get("modes", ["bus"]))
            writer.writerow(row)

    with open(os.path.join(DATA_DIR, "route_stops.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["route_id", "line_ref", "stop_sequence", "stop_id", "stop_name", "stop_name_ar", "lat", "lon"])
        writer.writeheader()
        writer.writerows(route_stops_tabular)

    print("[5/5] Success! All datasets generated:")
    print(f"  -> {os.path.join(DATA_DIR, 'rabat_bus_routes.geojson')} ({len(routes_geojson['features'])} route features)")
    print(f"  -> {os.path.join(DATA_DIR, 'rabat_bus_stops.geojson')} ({len(stops_geojson['features'])} bus stops)")
    print(f"  -> {os.path.join(DATA_DIR, 'routes.csv')} (Routes tabular)")
    print(f"  -> {os.path.join(DATA_DIR, 'stops.csv')} (Stops tabular)")
    print(f"  -> {os.path.join(DATA_DIR, 'route_stops.csv')} (Sequence schedule tabular)")
    print(f"  -> {os.path.join(DATA_DIR, 'network_summary.json')} (Network analytics)")

if __name__ == "__main__":
    process_network()
