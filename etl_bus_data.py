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

# Wiki catalog rows sometimes store "L22:Bab Chellah" in from/to instead of the place name.
_WIKI_LINE_PREFIX = re.compile(r"^L\s*\d+[A-Za-z]*\s*:\s*", re.IGNORECASE)

# Outer communes are matched before shared Rabat termini (Bab El Had, Bab Chellah),
# so a rural end does not inherit the urban zone from the city gate in the name.
_SOUTH_TERMS = (
    "temara", "témara", "harhoura", "skhirat",
    "ain atik", "ain attiq", "ain aouda", "ain el aouda",
    "mers el kheir", "tamesna",
)
_NORTH_TERMS = (
    "salé", "sale", "karia", "bouknadel", "sidi taibi",
)
_URBAN_TERMS = (
    "fath", "ennahda", "agdal", "chellah", "bab el had",
)
_AIRPORT_TERMS = ("aeroport", "aéroport", "airport")


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


def _contains_term(text, term):
    """Match a place name on token boundaries (so 'had' is not found inside 'lhad')."""
    return re.search(
        r"(?<!\w)" + re.escape(term) + r"(?!\w)",
        text,
        flags=re.IGNORECASE,
    ) is not None


def _any_term(text, terms):
    return any(_contains_term(text, term) for term in terms)


def strip_wiki_line_prefix(value):
    """Drop a leading wiki marker such as 'L22:' from an origin or destination."""
    if value is None:
        return None
    return _WIKI_LINE_PREFIX.sub("", str(value).strip()).strip()


def fetch_wiki_relations():
    """Fetches the official catalog of ALSA Rabat-Salé-Témara relations from OpenStreetMap Wiki.

    Relation ids come from the wiki table. Line ref and mode are provisional here;
    process_network replaces them with OSM relation tags (route, ref) when those exist.
    """
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

            routes.append({
                "ref": current_ref,
                "raw_ref": current_ref,
                "mode": None,
                "name": name,
                "relation_id": rel_id
            })

    print(f"  Found {len(routes)} route relations across {len(set(r['raw_ref'] for r in routes))} catalog refs.")
    return routes


def fetch_relation_data(rel_id, retries=3, backoff_factor=1.0):
    """Fetch a relation JSON document from the OSM API or the local cache.

    Returns (data, None) on success. Returns (None, skip) when the relation
    cannot be used. HTTP 410 Gone is not retried: the relation was deleted.
    """
    cache_path = os.path.join(RAW_DIR, f"relation_{rel_id}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f), None
        except json.JSONDecodeError as e:
            return None, {
                "relation_id": rel_id,
                "reason": f"invalid cached JSON: {e}",
            }

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
                return data, None
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 410:
                print(f"    Warning: Relation {rel_id} is gone (HTTP 410); not retrying.")
                return None, {
                    "relation_id": rel_id,
                    "reason": "HTTP 410 Gone",
                    "http_status": 410,
                }
            if attempt < retries:
                sleep_sec = backoff_factor * (2 ** (attempt - 1))
                print(f"    Notice: Retry {attempt}/{retries} for relation {rel_id} after {sleep_sec}s: {e}")
                time.sleep(sleep_sec)
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                sleep_sec = backoff_factor * (2 ** (attempt - 1))
                print(f"    Notice: Retry {attempt}/{retries} for relation {rel_id} after {sleep_sec}s: {e}")
                time.sleep(sleep_sec)
        except TimeoutError as e:
            last_err = e
            if attempt < retries:
                sleep_sec = backoff_factor * (2 ** (attempt - 1))
                print(f"    Notice: Retry {attempt}/{retries} for relation {rel_id} after {sleep_sec}s: {e}")
                time.sleep(sleep_sec)
        except json.JSONDecodeError as e:
            print(f"    Error: Invalid JSON received for relation {rel_id}: {e}")
            return None, {"relation_id": rel_id, "reason": f"invalid JSON: {e}"}
        except Exception as e:
            print(f"    Error: Unexpected failure fetching relation {rel_id}: {e}")
            return None, {"relation_id": rel_id, "reason": f"unexpected failure: {e}"}

    print(f"    Warning: Failed to fetch relation {rel_id} after {retries} attempts: {last_err}")
    return None, {
        "relation_id": rel_id,
        "reason": f"failed after {retries} attempts: {last_err}",
    }


def _way_node_ids(way, nodes_dict):
    return [n for n in way.get("nodes", []) if n in nodes_dict]


def stitch_ways_into_coordinates(way_ids, ways_dict, nodes_dict):
    """Chain OSM ways into polylines by shared endpoint node ids.

    Member order is not required. Two ways are joined only when they share an
    endpoint and that node connects exactly two way-ends, so a branch (three or
    more ways at one node) stays a separate segment instead of a false U-turn.
    Reversed ways drop the shared vertex (`[1:]` on the reversed sequence) and
    keep the far endpoint.
    """
    prepared = []
    for w_id in way_ids:
        way = ways_dict.get(w_id)
        if not way:
            continue
        node_ids = _way_node_ids(way, nodes_dict)
        if len(node_ids) < 2:
            continue
        prepared.append(node_ids)

    if not prepared:
        return []

    # node id -> [(way_index, at_start)]
    endpoint_refs = {}
    for index, node_ids in enumerate(prepared):
        endpoint_refs.setdefault(node_ids[0], []).append((index, True))
        if node_ids[-1] != node_ids[0]:
            endpoint_refs.setdefault(node_ids[-1], []).append((index, False))

    degree = {node_id: len(refs) for node_id, refs in endpoint_refs.items()}
    used = [False] * len(prepared)

    def append_oriented(chain, node_ids, at_tail):
        endpoint = chain[-1] if at_tail else chain[0]
        if at_tail:
            if node_ids[0] == endpoint:
                chain.extend(node_ids[1:])
            elif node_ids[-1] == endpoint:
                # Drop the joining vertex, keep the far endpoint.
                chain.extend(list(reversed(node_ids))[1:])
        else:
            if node_ids[-1] == endpoint:
                return node_ids[:-1] + chain
            elif node_ids[0] == endpoint:
                return list(reversed(node_ids))[:-1] + chain
        return chain

    def extend(chain, at_tail):
        while True:
            endpoint = chain[-1] if at_tail else chain[0]
            if degree.get(endpoint, 0) != 2:
                return chain
            nxt = None
            for index, _at_start in endpoint_refs.get(endpoint, []):
                if not used[index]:
                    nxt = index
                    break
            if nxt is None:
                return chain
            used[nxt] = True
            chain = append_oriented(chain, prepared[nxt], at_tail)
        return chain

    segments = []
    for index, node_ids in enumerate(prepared):
        if used[index]:
            continue
        used[index] = True
        chain = extend(list(node_ids), at_tail=True)
        chain = extend(chain, at_tail=False)
        segments.append([
            [round(nodes_dict[n]["lon"], 6), round(nodes_dict[n]["lat"], 6)]
            for n in chain
        ])

    return segments


def classify_zone(ref, name):
    """Classifies route into geographic zones: Rabat, Sale, Temara, Coastal, Interurban.

    Outer place names are tested before Bab El Had / Bab Chellah. Those gates
    are urban termini shared by rural lines, so a bare substring such as "had"
    must not pull Sidi Taibi, Ain Aouda, Mers El Kheir or Tamesna into the core.
    """
    n = (name or "").lower()
    r = str(ref or "").upper().strip()
    if "AE" in r or any(term in n for term in _AIRPORT_TERMS):
        return "Airport Express"
    if r.startswith("T") and r[1:].isdigit():
        return "Tramway"
    if r.startswith("20") or _any_term(n, _NORTH_TERMS):
        return "Salé & North"
    if r.startswith("30") or _any_term(n, _SOUTH_TERMS):
        return "Témara, Harhoura & South"
    if r.startswith("10") or _any_term(n, _URBAN_TERMS):
        return "Rabat Urban Core"
    return "Intercommunal"


def resolve_line_identity(r_meta, rel_tags):
    """Prefer OSM route/ref tags over the wiki catalog and the old name heuristic."""
    route_tag = (rel_tags.get("route") or "").strip().lower()
    ref_tag = (rel_tags.get("ref") or "").strip()
    mode = "tram" if route_tag == "tram" else "bus"
    line_ref = ref_tag or str(r_meta.get("raw_ref") or r_meta.get("ref") or "").strip()
    if mode == "tram":
        upper = line_ref.upper()
        if upper.startswith("T") and upper[1:].isdigit():
            line_ref = "T" + upper[1:]
        elif line_ref.isdigit():
            line_ref = f"T{line_ref}"
    return line_ref, mode


def is_transit_stop_member(role, node):
    """True for stop/platform roles, and for untagged roles that are still stops.

    Empty-role members used to be dropped entirely. Stations such as Stade Al Madina
    are members with role "" and public_transport=stop_position or platform.
    """
    if node is None:
        return False
    role = role or ""
    if "stop" in role or "platform" in role:
        return True
    if role != "":
        return False
    tags = node.get("tags") or {}
    public_transport = (tags.get("public_transport") or "").lower()
    if public_transport in ("stop_position", "platform"):
        return True
    highway = (tags.get("highway") or "").lower()
    if highway in ("bus_stop", "platform"):
        return True
    railway = (tags.get("railway") or "").lower()
    if railway in ("tram_stop", "platform", "halt", "stop"):
        return True
    return False


def _record_skip(skipped_relations, rel_id, r_meta, reason, http_status=None):
    entry = {
        "relation_id": rel_id,
        "ref": r_meta.get("raw_ref") or r_meta.get("ref"),
        "name": r_meta.get("name"),
        "reason": reason,
    }
    if http_status is not None:
        entry["http_status"] = http_status
    skipped_relations.append(entry)
    print(f"    Skipped relation {rel_id} ({entry['ref']}): {reason}")


def _line_sort_key(ref):
    text = str(ref)
    return (len(text), text)


def process_network():
    """Main ETL execution."""
    routes_catalog = fetch_wiki_relations()

    print(f"[2/5] Downloading & caching full OSM relations ({len(routes_catalog)} items)...")
    raw_records = []
    skipped_relations = []
    for idx, r_meta in enumerate(routes_catalog):
        rel_id = r_meta["relation_id"]
        data, skip = fetch_relation_data(rel_id)
        if data:
            raw_records.append((r_meta, data))
        else:
            reason = (skip or {}).get("reason", "fetch failed")
            _record_skip(
                skipped_relations,
                rel_id,
                r_meta,
                reason,
                http_status=(skip or {}).get("http_status"),
            )
        # Small delay to respect OSM API guidelines
        time.sleep(0.15)
        if (idx + 1) % 15 == 0 or idx == len(routes_catalog) - 1:
            print(f"  Downloaded {idx + 1}/{len(routes_catalog)} relations...")

    print(f"[3/5] Parsing geometries, reconstructing routes, and indexing transit targets...")

    processed_routes = []
    unique_stops = {}  # stop_id -> stop dict
    route_stops_tabular = []
    routes_tabular = []

    for r_meta, data in raw_records:
        rel_id = r_meta["relation_id"]

        elements = data.get("elements", [])
        nodes = {el["id"]: el for el in elements if el["type"] == "node"}
        ways = {el["id"]: el for el in elements if el["type"] == "way"}

        # Find relation element
        rel_el = next((el for el in elements if el["type"] == "relation"), None)
        if not rel_el:
            _record_skip(skipped_relations, rel_id, r_meta, "relation element missing from payload")
            continue

        rel_tags = rel_el.get("tags", {})
        line_ref, mode = resolve_line_identity(r_meta, rel_tags)
        r_meta["ref"] = line_ref
        r_meta["mode"] = mode
        members = rel_el.get("members", [])

        # Extract ordered stops
        stops_in_route = []
        way_refs = []

        for m in members:
            m_type = m.get("type")
            m_role = m.get("role", "") or ""
            m_ref = m.get("ref")

            if m_type == "node" and is_transit_stop_member(m_role, nodes.get(m_ref)):
                node = nodes.get(m_ref)
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
        origin = strip_wiki_line_prefix(rel_tags.get("from"))
        destination = strip_wiki_line_prefix(rel_tags.get("to"))
        if not origin or not destination:
            parts = official_name.split("–") if "–" in official_name else official_name.split("-")
            if len(parts) >= 2:
                origin = origin or strip_wiki_line_prefix(parts[0])
                destination = destination or strip_wiki_line_prefix(parts[-1])
            else:
                origin = origin or "Départ"
                destination = destination or "Terminus"

        zone = classify_zone(line_ref, " ".join(p for p in (official_name, origin, destination) if p))
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
            "color": "#E63946",
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
            "color": "#E63946"
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

    catalog_lines = sorted({str(r["ref"]) for r in routes_catalog}, key=_line_sort_key)
    exported_lines = sorted({r["line_ref"] for r in processed_routes}, key=_line_sort_key)
    palette_refs = sorted(set(catalog_lines) | set(exported_lines), key=_line_sort_key)
    color_map = {ref: LINE_COLORS[i % len(LINE_COLORS)] for i, ref in enumerate(palette_refs)}
    color_map["T1"] = "#1E40AF"  # Official Cobalt Blue for Tram T1
    color_map["T2"] = "#BE185D"  # Official Magenta/Rose for Tram T2
    for rec, tab in zip(processed_routes, routes_tabular):
        color = color_map.get(rec["line_ref"], "#E63946")
        rec["color"] = color
        tab["color"] = color

    skipped_relations.sort(key=lambda item: item.get("relation_id") or 0)

    print(f"  Processed {len(processed_routes)} routes across {len(exported_lines)} active lines (catalog: {len(catalog_lines)} lines) and {len(unique_stops)} unique bus stops.")
    if skipped_relations:
        print(f"  Skipped {len(skipped_relations)} relations; recorded in network_summary.json.")

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
                "lines": sorted(s["lines"], key=_line_sort_key),
                "lines_str": ", ".join(sorted(s["lines"], key=_line_sort_key)),
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

    # Compact GeoJSON: indented output is several times larger and is what the map downloads.
    def dump_geojson(path, payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")

    dump_geojson(os.path.join(DATA_DIR, "rabat_bus_routes.geojson"), routes_geojson)
    dump_geojson(os.path.join(DATA_DIR, "rabat_bus_stops.geojson"), stops_geojson)

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
            "zones": sorted({r["zone"] for r in processed_routes}),
            "skipped_relation_count": len(skipped_relations),
            "skipped_relations": skipped_relations,
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
        f.write("\n")

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
