"""
This module provides core functionalities for the Thirsty project,
including GPX parsing, Overpass API queries, and POI filtering.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from pathlib import Path

import folium
from folium.plugins import LocateControl
import gpxpy
import requests
import rich.console
import rich.progress
from scipy.spatial import KDTree
from rich.markup import escape

console = rich.console.Console()


CACHE_DIR = Path.home() / ".cache" / "thirsty" / "overpass"
CACHE_TTL_SECONDS = 7 * 24 * 3600


def _cache_path(query: str) -> Path:
    key = hashlib.sha256(query.encode()).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_cache(query: str):
    path = _cache_path(query)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(query: str, elements: list) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(query).write_text(json.dumps(elements))


def purge_cache() -> int:
    if not CACHE_DIR.exists():
        return 0
    now = time.time()
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        if now - path.stat().st_mtime > CACHE_TTL_SECONDS:
            path.unlink()
            removed += 1
    return removed


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter"
]

# overpass-api.de requires applications to identify themselves.
# https://wiki.openstreetmap.org/wiki/Overpass_API
OVERPASS_HEADERS = {
    "User-Agent": "thirsty/0.1.0 (https://github.com/berettavexee/thirsty)",
    "Referer": "https://github.com/berettavexee/thirsty",
}

# Approximate degrees per meter at mid-latitudes (111.32 km/degree).
APPROX_DEGREES_PER_METER = 1 / 111320.0

# Safety margins applied when converting metric distances to degree-space for KDTree queries.
# 5 % extra so a POI at exactly max_distance_m is never dropped by floating-point rounding.
_KDTREE_SEARCH_MARGIN = 1.05
# 5 % dilation applied to each bbox before testing whether it contains GPX points.
_BBOX_DILATION_FACTOR = 0.05
# Additional radius factor for the KDTree ball query inside bbox containment check.
_KDTREE_BBOX_RADIUS_MARGIN = 1.1

# Fixed tile size in degrees for the Overpass query grid.
# Tiles are aligned to a global grid (multiples of TILE_SIZE_DEG), so any two
# routes crossing the same area produce identical tile coordinates and hit the cache.
TILE_SIZE_DEG = 0.5

# (south, west, north, east) in decimal degrees
Bbox = tuple[float, float, float, float]

AMENITIES = {
    "water": "[amenity=drinking_water]",
    "point": "[amenity=water_point][drinking_water=yes]",
    "tap": "[man_made=water_tap][drinking_water=yes]",
    "spring": "[natural=spring][drinking_water=yes]",
    "fountain": "[amenity=fountain][drinking_water=yes]",
    "bakery": "[shop=bakery]",
    "cafe": "[amenity=cafe]",
    "fuel_convenience": "[amenity=fuel]",
    "convenience_store": "[shop=convenience]",
    "pizza_vending": "[amenity=vending_machine]",
}

# Maps canonical POI class → Folium icon, GPX symbol, and human-readable label.
_POI_STYLES: dict[str, dict] = {
    "bakery":      {"icon_color": "green",   "icon_name": "cutlery",         "gpx_symbol": "food",       "label": "Bakery"},
    "water":       {"icon_color": "blue",    "icon_name": "tint",            "gpx_symbol": "water-drop", "label": "Water"},
    "cafe":        {"icon_color": "darkred", "icon_name": "coffee",          "gpx_symbol": "meals",      "label": "Cafe"},
    "fuel":        {"icon_color": "orange",  "icon_name": "car",             "gpx_symbol": "gas",        "label": "Fuel Station"},
    "convenience": {"icon_color": "purple",  "icon_name": "shopping-cart",   "gpx_symbol": "store",      "label": "Convenience Store"},
    "vending":     {"icon_color": "darkred", "icon_name": "shopping-basket", "gpx_symbol": "pizza",      "label": "Vending Machine"},
    "unknown":     {"icon_color": "darkblue","icon_name": "info-sign",       "gpx_symbol": "generic",    "label": None},
}


def classify_poi(poi: dict) -> str:
    """Return the canonical class for a POI based on its Overpass tags."""
    tags = poi.get("tags", {})
    amenity = tags.get("amenity")
    shop = tags.get("shop")
    natural = tags.get("natural")
    man_made = tags.get("man_made")

    if shop == "bakery":
        return "bakery"
    if (amenity in ("drinking_water", "water_point", "fountain")
            or natural == "spring"
            or (man_made == "water_tap" and tags.get("drinking_water") == "yes")):
        return "water"
    if amenity == "cafe":
        return "cafe"
    if amenity == "fuel":
        return "fuel"
    if shop == "convenience":
        return "convenience"
    if amenity == "vending_machine":
        return "vending"
    return "unknown"


def _poi_type_label(poi: dict) -> str:
    """Human-readable type string for map popups and GPX descriptions."""
    style = _POI_STYLES[classify_poi(poi)]
    if style["label"]:
        return style["label"]
    tags = poi.get("tags", {})
    for key in ("amenity", "shop", "natural", "man_made"):
        val = tags.get(key)
        if val:
            return val.replace("_", " ").title()
    vending = tags.get("vending")
    if vending:
        return f"Vending Machine: {vending.replace('_', ' ').title()}"
    return "Type inconnu"


def display_gpx_on_map(
    data: gpxpy.gpx.GPX,
    pois: list[dict],
    bboxes_to_display: list[Bbox] | None = None,
) -> folium.Map:
    """Display the GPX route and POIs on a Folium map.

    Args:
        data: Parsed GPX object containing the track.
        pois: List of Overpass POI elements (each with 'lat', 'lon', 'tags').
        bboxes_to_display: Optional list of bboxes to draw as semi-transparent red rectangles.

    Returns:
        A Folium Map object ready to be saved as HTML.
    """
    if bboxes_to_display is None:
        bboxes_to_display = []

    # Create a base map centered around the middle of the GPX track
    track_latitudes = [point.latitude
                       for track in data.tracks
                       for segment in track.segments
                       for point in segment.points]

    track_longitudes = [point.longitude
                        for track in data.tracks
                        for segment in track.segments
                        for point in segment.points]

    center_lat = sum(track_latitudes) / len(track_latitudes)
    center_lon = sum(track_longitudes) / len(track_longitudes)

    map_center = [center_lat, center_lon]
    folium_map = folium.Map(location=map_center, zoom_start=12)
    LocateControl(auto_start=False).add_to(folium_map)

    # Plot the GPX track on the map
    for track in data.tracks:
        for segment in track.segments:
            # Create a list of coordinates from the GPX track segment
            track_coords = [(point.latitude, point.longitude)
                            for point in segment.points]
            folium.PolyLine(track_coords, color="blue",
                            weight=2.5, opacity=1).add_to(folium_map)

    # Plot BBoxes on the map
    if bboxes_to_display:
        for bbox in bboxes_to_display:
            south, west, north, east = bbox
            # Les coins du rectangle : [SW, NW, NE, SE, SW]
            bounds_coords = [
                (south, west),
                (north, west),
                (north, east),
                (south, east),
                (south, west)  # Fermer le polygone
            ]
            folium.Polygon(
                locations=bounds_coords,
                color="red",
                weight=2,
                fill=True,
                fill_color="red",
                fill_opacity=0.1
            ).add_to(folium_map)
        console.print(
            f"✅ Displayed {len(bboxes_to_display)} Overpass BBoxes on the map.")

    # Plot POIs on the map
    for poi in pois:
        style = _POI_STYLES[classify_poi(poi)]
        poi_name = poi['tags'].get('name', 'POI sans nom')
        folium.Marker(
            location=[poi['lat'], poi['lon']],
            popup=folium.Popup(f"{poi_name}: {_poi_type_label(poi)}", max_width=300),
            icon=folium.Icon(color=style["icon_color"], icon=style["icon_name"], prefix='fa')
        ).add_to(folium_map)

    return folium_map


def download_gpx(url: str) -> io.BytesIO:
    """Download GPX from URL."""

    console.print(f"⏳ Downloading GPX from {url}")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get("Content-Length", 0))

    data = io.BytesIO()

    with rich.progress.Progress() as progress:
        task = progress.add_task("[cyan] Downloading", total=total_size)

        for chunk in response.iter_content(chunk_size=1024):
            data.write(chunk)
            progress.update(task, advance=len(chunk))

    data.seek(0)
    return data


def get_bounds(gpx: gpxpy.gpx.GPX, max_distance_m: float) -> Bbox | None:
    """Return the bounding box of all GPX track points, expanded by max_distance_m.

    Args:
        gpx: Parsed GPX object.
        max_distance_m: Search radius in metres; the bbox is expanded by this amount on each side.

    Returns:
        (south, west, north, east) in decimal degrees, or None if the track has no points.
    """
    min_lat = float('inf')
    max_lat = float('-inf')
    min_lon = float('inf')
    max_lon = float('-inf')

    angular_margin = max_distance_m * APPROX_DEGREES_PER_METER * _KDTREE_SEARCH_MARGIN

    found_points = False
    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                found_points = True
                min_lat = min(min_lat, pt.latitude)
                max_lat = max(max_lat, pt.latitude)
                min_lon = min(min_lon, pt.longitude)
                max_lon = max(max_lon, pt.longitude)

    if not found_points:
        return None

    min_lat -= angular_margin
    max_lat += angular_margin
    min_lon -= angular_margin
    max_lon += angular_margin

    return min_lat, min_lon, max_lat, max_lon




def _bbox_contains_gpx_points(
    bbox: Bbox,
    gpx_kdtree: KDTree,
    gpx_points_coords: list[tuple[float, float]],
) -> bool:
    """Return True if bbox (dilated by _BBOX_DILATION_FACTOR) contains at least one GPX point.

    Args:
        bbox: (south, west, north, east) bounding box to test.
        gpx_kdtree: KDTree built from gpx_points_coords for fast spatial lookup.
        gpx_points_coords: List of (lat, lon) tuples of all GPX track points.
    """
    south, west, north, east = bbox

    lat_margin = (north - south) * _BBOX_DILATION_FACTOR
    lon_margin = (east - west) * _BBOX_DILATION_FACTOR

    dilated_south = south - lat_margin
    dilated_north = north + lat_margin
    dilated_west = west - lon_margin
    dilated_east = east + lon_margin

    center_lat = (dilated_south + dilated_north) / 2
    center_lon = (dilated_west + dilated_east) / 2

    diagonal_lat_deg = dilated_north - dilated_south
    diagonal_lon_deg = dilated_east - dilated_west
    approx_bbox_radius_deg = math.sqrt(diagonal_lat_deg**2 + diagonal_lon_deg**2) / 2

    potential_indices = gpx_kdtree.query_ball_point(
        [center_lat, center_lon], r=approx_bbox_radius_deg * _KDTREE_BBOX_RADIUS_MARGIN)

    for idx in potential_indices:
        lat, lon = gpx_points_coords[idx]
        if dilated_south <= lat <= dilated_north and dilated_west <= lon <= dilated_east:
            return True

    return False


def get_tiles(
    bbox: Bbox,
    gpx_kdtree: KDTree,
    gpx_points_coords: list[tuple[float, float]],
) -> list[Bbox]:
    """Return all TILE_SIZE_DEG × TILE_SIZE_DEG fixed-grid tiles that intersect the GPX track.

    Tiles are aligned to a global grid (coordinates are integer multiples of TILE_SIZE_DEG),
    so identical geographic areas always produce identical tile coordinates regardless of the
    GPX trace shape. This maximises Overpass response cache hits across different routes.

    Args:
        bbox: (south, west, north, east) overall bounding box to cover.
        gpx_kdtree: KDTree built from gpx_points_coords for fast spatial lookup.
        gpx_points_coords: List of (lat, lon) tuples of all GPX track points.

    Returns:
        Tiles that contain at least one GPX track point, ready to be sent to Overpass.
    """
    south, west, north, east = bbox

    i_south = math.floor(south / TILE_SIZE_DEG)
    i_west = math.floor(west / TILE_SIZE_DEG)
    i_north = math.ceil(north / TILE_SIZE_DEG)
    i_east = math.ceil(east / TILE_SIZE_DEG)

    tiles = []
    for i in range(i_south, i_north):
        for j in range(i_west, i_east):
            tile: Bbox = (
                i * TILE_SIZE_DEG,
                j * TILE_SIZE_DEG,
                (i + 1) * TILE_SIZE_DEG,
                (j + 1) * TILE_SIZE_DEG,
            )
            if _bbox_contains_gpx_points(tile, gpx_kdtree, gpx_points_coords):
                tiles.append(tile)
    return tiles


def query_overpass(bbox: Bbox, poi_types: list[str], gpx_kdtree: KDTree) -> list[dict]:
    """Query the Overpass API for POIs of the given types within bbox.

    Results are cached on disk for CACHE_TTL_SECONDS. On failure the query is
    retried across all OVERPASS_ENDPOINTS (2 full rotations before giving up).

    Args:
        bbox: (south, west, north, east) bounding box for the Overpass query.
        poi_types: List of AMENITIES keys (e.g. ["water", "bakery"]).
        gpx_kdtree: Unused directly here; reserved for future proximity pre-filtering.

    Returns:
        List of Overpass element dicts, each with at least 'id', 'lat', 'lon', 'tags'.

    Raises:
        requests.exceptions.RequestException: If all retry attempts fail.
    """
    south, west, north, east = bbox
    bbox_str = f"{south:.5f},{west:.5f},{north:.5f},{east:.5f}"

    query_parts = []
    for poi_type in poi_types:
        tag_filter = AMENITIES[poi_type]
        query_parts.append(f'node{tag_filter};')

    query = f"[out:json][timeout:90][bbox:{bbox_str}];(" + "".join(query_parts) + ");out center;"
    
    cached = _load_cache(query)
    if cached is not None:
        console.print(f"[green]Cache hit pour bbox {bbox_str}[/green]")
        return cached

    max_retries = len(OVERPASS_ENDPOINTS) * 2
    retry_delay = 5
    success_delay = 2

    for attempt in range(1, max_retries + 1):
        endpoint = OVERPASS_ENDPOINTS[(attempt - 1) % len(OVERPASS_ENDPOINTS)]

        try:
            console.print(f"Appel {attempt} : {endpoint} : {escape(query)}")
            response = requests.post(endpoint, data=query, headers=OVERPASS_HEADERS, timeout=95)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", retry_delay * 2))
                console.print(f"[bold yellow]Rate limit (429) sur {endpoint}, attente {wait}s...[/bold yellow]")
                time.sleep(wait)
                continue

            response.raise_for_status()

            data = response.json()
            remark = data.get("remark")
            if remark:
                console.print(f"[bold yellow]Overpass remark sur {endpoint}: {remark}[/bold yellow]")
                raise ValueError(f"Overpass server error: {remark}")

            elements = data.get("elements", [])
            _save_cache(query, elements)
            time.sleep(success_delay)
            return elements

        except (requests.exceptions.RequestException, ValueError) as e:
            console.print(f"[bold yellow]Tentative {attempt} échouée sur {endpoint}: {e}[/bold yellow]")

            if attempt < max_retries:
                console.print(f"Essai d'un autre serveur dans {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                console.print(f"[bold red]Erreur définitive après {max_retries} essais sur tous les serveurs.[/bold red]")
                raise


def add_waypoints_to_gpx(gpx: gpxpy.gpx.GPX, pois: list[dict]) -> gpxpy.gpx.GPX:
    """Add POI waypoints to a GPX object and return it."""

    for poi in pois:
        wpt = gpxpy.gpx.GPXWaypoint()
        wpt.latitude = poi["lat"]
        wpt.longitude = poi["lon"]
        style = _POI_STYLES[classify_poi(poi)]
        wpt.name = poi['tags'].get('name', 'POI sans nom')
        wpt.symbol = style["gpx_symbol"]
        wpt.description = f"{wpt.name} ({_poi_type_label(poi)})"
        gpx.waypoints.append(wpt)

    return gpx


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""

    R = 6371000  # Earth radius in meter
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi/2)**2 + math.cos(phi1) * \
        math.cos(phi2) * math.sin(d_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    return R * c


def deduplicate_pois_by_id(pois: list[dict]) -> list[dict]:
    """Remove duplicate POIs using their Overpass element ID.

    Args:
        pois: Raw list of Overpass elements, possibly containing duplicates
              when the same node appears in results from adjacent bboxes.

    Returns:
        New list keeping only the first occurrence of each unique 'id'.
    """
    console.print("⏳ Deduplicating POIs by Overpass ID...")

    seen_ids: set = set()
    unique_pois: list[dict] = []

    for poi in rich.progress.track(pois, description="Deduplicating POIs"):
        poi_id = poi.get('id')

        if poi_id is not None and poi_id not in seen_ids:
            unique_pois.append(poi)
            seen_ids.add(poi_id)
        elif poi_id is None:
            console.log(f"[yellow]Warning: POI without 'id' skipped: {poi}[/yellow]")

    console.print(f"✅ Deduplication complete. {len(unique_pois)} unique POIs out of {len(pois)} initial ones.")
    return unique_pois


def filter_pois_near_track(
    track_points_coords: list[tuple[float, float]],
    kdtree: KDTree,
    pois: list[dict],
    max_distance_m: float = 100,
) -> list[dict]:
    """Return only the POIs within max_distance_m of at least one GPX track point.

    Uses a KDTree pre-filter in degree-space to reduce the number of exact
    Haversine distance calculations.

    Args:
        track_points_coords: List of (lat, lon) tuples of all GPX track points.
        kdtree: KDTree built from track_points_coords.
        pois: Candidate POI list to filter.
        max_distance_m: Maximum allowed distance in metres from the track.

    Returns:
        Subset of pois whose nearest track point is within max_distance_m.
    """
    nearby_pois: list[dict] = []
    kdtree_radius_degrees = max_distance_m * APPROX_DEGREES_PER_METER * _KDTREE_SEARCH_MARGIN

    console.print(
        f"Filtering POIs near track (max_distance_m: {max_distance_m}m)...")
    for poi in rich.progress.track(pois, description="Filtering POI"):
        poi_lat, poi_lon = poi["lat"], poi["lon"]

        indices_in_range = kdtree.query_ball_point(
            [poi_lat, poi_lon], r=kdtree_radius_degrees)

        if indices_in_range:
            for idx in indices_in_range:
                track_point_lat, track_point_lon = track_points_coords[idx]
                if haversine(poi_lat, poi_lon, track_point_lat, track_point_lon) < max_distance_m:
                    nearby_pois.append(poi)
                    break

    console.print(f"Found {len(nearby_pois)} POIs near the track.")
    return nearby_pois


def sanitize_gpx_text(data: str) -> str:
    """Replace unescaped '&' with '&amp;' to produce valid GPX XML."""
    return re.sub(r'&(?!amp;|quot;|lt;|gt;|apos;)', '&amp;', data)


def process_gpx_and_pois(
    gpx_content: str,
    poi_types: list[str],
    max_distance_m: float,
    show_bboxes: bool = False,
    progress_callback: callable | None = None,
) -> tuple[gpxpy.gpx.GPX, list[dict], list[Bbox]]:
    """Orchestrate the full pipeline: parse GPX → query Overpass → deduplicate → filter.

    Overpass queries use a fixed TILE_SIZE_DEG × TILE_SIZE_DEG grid so that cache hits
    are maximised across different routes covering the same geographic area.

    Args:
        gpx_content: Raw GPX file content as a string.
        poi_types: List of AMENITIES keys to search for.
        max_distance_m: Maximum distance in metres from the track to retain a POI.
        show_bboxes: When True, the returned tile list is populated; otherwise it is empty.
        progress_callback: Optional callable(dict) receiving progress updates with keys
                           'stage', 'current', 'total', 'poi_count'.

    Returns:
        Tuple (gpx, filtered_pois, queried_tiles):
            - gpx: Parsed gpxpy.GPX object (track only, no waypoints yet).
            - filtered_pois: POIs within max_distance_m of the track.
            - queried_tiles: Tiles sent to Overpass (empty if show_bboxes is False).
    """
    # Helper function to safely call progress callback
    def report_progress(stage, current=0, total=0, poi_count=0):
        if progress_callback:
            try:
                progress_callback({
                    'stage': stage,
                    'current': current,
                    'total': total,
                    'poi_count': poi_count
                })
            except Exception as e:
                console.print(f"[yellow]Warning: Progress callback error: {e}[/yellow]")
    
    removed = purge_cache()
    if removed:
        console.print(f"🗑️  Cache : {removed} fichier(s) expirés supprimés.")

    # Stage 1: Parsing GPX
    report_progress('Parsing GPX', 0, 5, 0)
    gpx_content = sanitize_gpx_text(gpx_content)
    gpx = gpxpy.parse(gpx_content)
    console.print("✅ Successfully parsed GPX data.")

    bounds = get_bounds(gpx, max_distance_m)

    if bounds is None:
        console.print(
            "[bold yellow]Warning: No track points found in GPX data. Cannot determine bounding box for POI search.[/bold yellow]")
        return gpx, [], []  # Retourne aussi une liste vide pour les bboxes

    track_points_coords = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                track_points_coords.append((pt.latitude, pt.longitude))

    if not track_points_coords:
        console.print(
            "[bold yellow]Warning: No track points found in GPX data. POI search will be skipped.[/bold yellow]")
        return gpx, [], []  # Retourne aussi une liste vide pour les bboxes

    console.print("Building KD Tree for GPX track points.")
    gpx_kdtree = KDTree(track_points_coords)
    console.print("KDTree built.")

    console.print(f"Searching for POIs of type(s): {', '.join(poi_types)}")
    console.print(f"Tile size: {TILE_SIZE_DEG}°")

    report_progress('Calculating tiles', 1, 5, 0)
    bboxes = get_tiles(bounds, gpx_kdtree, track_points_coords)
    console.print(f"{len(bboxes)} tiles to query.")

    collected_bboxes = bboxes if show_bboxes else []

    # Stage 2: Find POIs
    total_tiles = len(bboxes)
    pois = []
    for idx, bbox in enumerate(bboxes):
        report_progress('Querying Overpass API', idx + 1, total_tiles, len(pois))
        pois.extend(query_overpass(bbox, poi_types, gpx_kdtree))

    console.print(f"Total raw POIs found by Overpass: {len(pois)}")

    # Stage 3: Remove duplicated POIs
    report_progress('Deduplicating POIs', 3, 5, len(pois))
    deduplicated_pois = deduplicate_pois_by_id(pois)
    console.print(f"Total unique POIs after deduplication: {len(deduplicated_pois)}")

    # Stage 4: Filter POIs
    report_progress('Filtering POIs by distance', 4, 5, len(deduplicated_pois))
    filtered_pois = filter_pois_near_track(
        track_points_coords, gpx_kdtree, deduplicated_pois, max_distance_m)
    console.print(f"Total POIs within {max_distance_m}m of track: {len(filtered_pois)}")

    # Stage 5: Complete
    report_progress('Complete', 5, 5, len(filtered_pois))

    # Retourne également la liste des bboxes collectées
    return gpx, filtered_pois, collected_bboxes
