"""
This module provides core functionalities for the Thirsty project,
including GPX parsing, Overpass API queries, and POI filtering.
"""

import io
import math
import re

import folium
import gpxpy
import requests
import rich.console
import rich.progress
from scipy.spatial import KDTree

console = rich.console.Console()


OVERPASS_URL = "http://overpass-api.de/api/interpreter"
# Environ 111.32 km par degré de latitud
APPROX_DEGREES_PER_METER = 1 / 111320.0

AMENITIES = {
    "water": "[amenity=drinking_water]",
    "point": "[amenity=water_point][drinking_water=yes]",
    "tap": "[man_made=water_tap][drinking_water=yes]",
    "spring": "[natural=spring][drinking_water=yes]",
    "fountain": "[amenity=fountain][drinking_water=yes]",
    "bakery": "[shop=bakery]",
    "cafe": "[amenity=cafe]",
    "fuel_convenience": "[amenity=fuel][shop=convenience]",
    "convenience_store": "[shop=convenience]",
    "pizza_vending": "[amenity=vending_machine][vending=pizza]",
}


def display_gpx_on_map(data, pois, bboxes_to_display=None):  # Ajout de bboxes_to_display
    """
    Display the GPX route and POIs on a map, optionally with Overpass BBoxes.
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
        icon_color = "darkblue"
        icon_name = "info-sign"

        # Récupérer les tags pertinents de manière sécurisée
        amenity_tag = poi['tags'].get('amenity')
        shop_tag = poi['tags'].get('shop')
        natural_tag = poi['tags'].get('natural')
        man_made_tag = poi['tags'].get('man_made')
        vending_tag = poi['tags'].get('vending')

        # Logique pour déterminer la couleur et l'icône
        if shop_tag == 'bakery':
            icon_color = "green"
            icon_name = "cutlery"
        elif amenity_tag in ['drinking_water', 'water_point', 'fountain'] or \
                natural_tag == 'spring' or \
                (man_made_tag == 'water_tap' and poi['tags'].get('drinking_water') == 'yes'):
            icon_color = "blue"
            icon_name = "tint"
        elif amenity_tag == 'cafe':
            icon_color = "darkred"
            icon_name = "coffee"
        elif amenity_tag == 'fuel' and shop_tag == 'convenience':
            icon_color = "orange"
            icon_name = "gas-pump"
        elif shop_tag == 'convenience' and amenity_tag != 'fuel':
            icon_color = "purple"
            icon_name = "shopping-cart"
        elif amenity_tag == 'vending_machine' and vending_tag == 'pizza':
            icon_color = "darkred"
            icon_name = "pizza-slice"

        # Créer le contenu du popup de manière robuste
        poi_name = poi['tags'].get('name', 'POI sans nom')

        # Pour l'affichage dans le popup, on essaie de trouver le type le plus pertinent
        if amenity_tag:
            poi_type_display = amenity_tag
        elif shop_tag:
            poi_type_display = shop_tag
        elif natural_tag:
            poi_type_display = natural_tag
        elif man_made_tag:
            poi_type_display = man_made_tag
        elif vending_tag:
            poi_type_display = f"vending_machine ({vending_tag})"
        else:
            poi_type_display = 'Type inconnu'

        folium.Marker(
            location=[poi['lat'], poi['lon']],
            popup=folium.Popup(
                f"{poi_name}: {poi_type_display}", max_width=300),
            icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')
        ).add_to(folium_map)

    return folium_map


def download_gpx(url):
    """
    Download GPX from URL
    """

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


def get_bounds(gpx, max_distance_m):
    """
    Return GPX trace bounding box [south, west, north, est]
    Optimized to iterate over points only once.
    """
    # Initialize min/max values with extreme values
    min_lat = float('inf')
    max_lat = float('-inf')
    min_lon = float('inf')
    max_lon = float('-inf')

    # Compute angular margin use to expand the bounds
    approx_degrees_per_meter = 1 / 111320.0
    angular_margin = max_distance_m * approx_degrees_per_meter * 1.5

    # Flag to check if any points were found
    found_points = False

    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                found_points = True
                # Update min/max latitude
                min_lat = min(min_lat, pt.latitude)
                max_lat = max(max_lat, pt.latitude)
                # Update min/max longitude
                min_lon = min(min_lon, pt.longitude)
                max_lon = max(max_lon, pt.longitude)

    if not found_points:
        return None

    min_lat -= angular_margin
    max_lat += angular_margin
    min_lon -= angular_margin
    max_lon += angular_margin

    return min_lat, min_lon, max_lat, max_lon


def _subdivide_bbox(bbox, lat_divisions, lon_divisions):
    """
    Subdivides a given bounding box into a grid of smaller bounding boxes.

    Args:
        bbox (tuple): A tuple (south, west, north, east) representing the bounding box.
        lat_divisions (int): Number of divisions along the latitude (rows).
        lon_divisions (int): Number of divisions along the longitude (columns).

    Returns:
        list: A list of smaller bounding box tuples.
    """
    south, west, north, east = bbox
    sub_bboxes = []

    lat_step = (north - south) / lat_divisions
    lon_step = (east - west) / lon_divisions

    for i in range(lat_divisions):
        for j in range(lon_divisions):
            sub_south = south + i * lat_step
            sub_north = south + (i + 1) * lat_step
            sub_west = west + j * lon_step
            sub_east = west + (j + 1) * lon_step
            sub_bboxes.append((sub_south, sub_west, sub_north, sub_east))
    return sub_bboxes


# max_distance_m retiré des paramètres
def _bbox_contains_gpx_points(bbox, gpx_kdtree, gpx_points_coords):
    """
    Checks if a bounding box (with a 10% margin) contains any GPX track points.

    Args:
        bbox (tuple): (south, west, north, east)
        gpx_kdtree (KDTree): KDTree of GPX track points.
        gpx_points_coords (list): List of (lat, lon) tuples for GPX points.

    Returns:
        bool: True if the bbox (with margin) contains at least one GPX point, False otherwise.
    """
    south, west, north, east = bbox

    # Calculer la marge de 10% de la taille de la bbox
    lat_margin = (north - south) * 0.05
    lon_margin = (east - west) * 0.05

    # Dilater la bbox
    dilated_south = south - lat_margin
    dilated_north = north + lat_margin
    dilated_west = west - lon_margin
    dilated_east = east + lon_margin

    # Calculer le centre de la BBox dilatée et sa diagonale pour la requête KDTree
    center_lat = (dilated_south + dilated_north) / 2
    center_lon = (dilated_west + dilated_east) / 2

    diagonal_lat_deg = dilated_north - dilated_south
    diagonal_lon_deg = dilated_east - dilated_west
    approx_bbox_radius_deg = math.sqrt(
        diagonal_lat_deg**2 + diagonal_lon_deg**2) / 2

    # Utiliser un rayon légèrement plus grand pour la requête KDTree afin d'être sûr de couvrir
    # La marge de 1.1 est une précaution supplémentaire pour s'assurer que le KDTree couvre bien toute la zone dilatée.
    potential_indices = gpx_kdtree.query_ball_point(
        [center_lat, center_lon], r=approx_bbox_radius_deg * 1.1)

    for idx in potential_indices:
        lat, lon = gpx_points_coords[idx]
        # Vérifier si le point GPX est dans la BBox DILATÉE
        if dilated_south <= lat <= dilated_north and dilated_west <= lon <= dilated_east:
            return True

    return False


def get_relevant_bboxes(bbox, gpx_kdtree, gpx_points_coords, max_bbox_area_sq_deg=0.5, lat_divisions=2, lon_divisions=2):
    """
    Recursively counts the number of relevant bounding boxes that will be processed
    (either queried directly or skipped due to no GPX points).
    This count will be used as the 'total' for the rich progress bar.
    """
    south, west, north, east = bbox
    current_bbox_area = (north - south) * (east - west)

    if not _bbox_contains_gpx_points(bbox, gpx_kdtree, gpx_points_coords):
        return []

    if current_bbox_area <= max_bbox_area_sq_deg:
        return [bbox]
    
    sub_bboxes = _subdivide_bbox(bbox, lat_divisions, lon_divisions)
    bboxes = []
    for sub_bbox in sub_bboxes:
        bboxes.extend(get_relevant_bboxes(sub_bbox, gpx_kdtree, gpx_points_coords,
                      max_bbox_area_sq_deg, lat_divisions, lon_divisions))
    return bboxes


def query_overpass(bbox, poi_types, gpx_kdtree):
    """
    Generate an Overpass QL query for potable drinking water POIs,
    handling large bounding boxes by subdividing them and checking for GPX track presence.

    Args:
        bbox (tuple): A tuple (south, west, north, east) representing the bounding box.
        poi_types (list): A list of POI types (e.g., ["water", "fountain", "bakery"]).
        gpx_kdtree (KDTree): KDTree of GPX track points.

    Returns:
        list: A list of dictionaries, where each dictionary represents a POI.
    """
    south, west, north, east = bbox

    bbox_str = f"({south:.5f},{west:.5f},{north:.5f},{east:.5f})"
    # console.print(f"  Executing Overpass query for bbox: {bbox_str}...")

    query_parts = []
    for poi_type in poi_types:
        tag_filter = AMENITIES[poi_type]
        query_parts.append(f'node{tag_filter}{bbox_str};')

    query = "[out:json][timeout:90];(" + "".join(query_parts) + ");out center;"
    try:
        response = requests.post(OVERPASS_URL, data=query, timeout=95)
        response.raise_for_status()
        elements = response.json()["elements"]
        # console.print(f"  Found {len(elements)} elements in this bbox.")
        return elements
    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]Error during Overpass query: {e}[bold red]")
        raise


def add_waypoints_to_gpx(gpx, pois):
    """
    Add POI to GPX trace
    """

    for poi in pois:
        wpt = gpxpy.gpx.GPXWaypoint()
        wpt.latitude = poi["lat"]
        wpt.longitude = poi["lon"]

        poi_name = poi['tags'].get('name', 'POI sans nom')
        amenity_tag = poi['tags'].get('amenity')
        shop_tag = poi['tags'].get('shop')
        natural_tag = poi['tags'].get('natural')
        man_made_tag = poi['tags'].get('man_made')
        vending_tag = poi['tags'].get('vending')

        if shop_tag == 'bakery':
            wpt.symbol = "food"
            wpt.name = poi_name
            wpt.description = poi_name + " (Bakery)"
        elif amenity_tag == 'cafe':
            wpt.symbol = "meals"
            wpt.name = poi_name
            wpt.description = poi_name + " (Cafe)"
        elif amenity_tag == 'fuel' and shop_tag == 'convenience':
            wpt.symbol = "gas"
            wpt.name = poi_name
            wpt.description = poi_name + " (Fuel with Convenience Store)"
        elif shop_tag == 'convenience' and amenity_tag != 'fuel':
            wpt.symbol = "store"
            wpt.name = poi_name
            wpt.description = poi_name + " (Convenience Store)"
        elif amenity_tag == 'vending_machine' and vending_tag == 'pizza':
            wpt.symbol = "pizza"
            wpt.name = poi_name
            wpt.description = poi_name + " (Pizza Vending Machine)"
        elif amenity_tag in ['drinking_water', 'water_point', 'fountain'] or \
                natural_tag == 'spring' or \
                (man_made_tag == 'water_tap' and poi['tags'].get('drinking_water') == 'yes'):
            wpt.symbol = "water-drop"
            wpt.name = poi_name
            wpt.description = poi_name + " (Water)"
        else:
            wpt.symbol = "generic"
            wpt.name = poi_name
            if amenity_tag:
                wpt.description = poi_name + \
                    f" ({amenity_tag.replace('_', ' ').title()})"
            elif shop_tag:
                wpt.description = poi_name + \
                    f" ({shop_tag.replace('_', ' ').title()})"
            elif vending_tag:
                wpt.description = poi_name + \
                    f" (Vending Machine: {vending_tag.replace('_', ' ').title()})"
            else:
                wpt.description = poi_name + " (Unknown POI Type)"

        gpx.waypoints.append(wpt)

    return gpx


def haversine(lat1, lon1, lat2, lon2):
    """
    Return distance in meter between two GPS points
    """

    R = 6371000  # Earth radius in meter
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi/2)**2 + math.cos(phi1) * \
        math.cos(phi2) * math.sin(d_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    return R * c


def deduplicate_pois_by_id(pois):
    """
    Removes duplicate POIs from a list using their unique Overpass ID.

    Args:
        pois (list): A list of dictionaries, where each dictionary represents a POI
                     and must contain an 'id' key (the Overpass ID of the POI).

    Returns:
        list: A new list of dictionaries, containing only the unique POIs.
    """
    console.print("⏳ Deduplicating POIs by Overpass ID...")

    seen_ids = set()  # A set to store already seen POI IDs.
    unique_pois = []  # The list that will hold the unique POIs.

    for poi in rich.progress.track(pois, description="Deduplicating POIs"):
        poi_id = poi.get('id')  # Retrieve the POI's ID.

        # Check if the ID has already been seen.
        if poi_id is not None and poi_id not in seen_ids:
            unique_pois.append(poi)  # Add the POI to the unique list.
            seen_ids.add(poi_id)      # Add the ID to the set of seen IDs.
        elif poi_id is None:
            # Handle cases where a POI might not have an ID (unlikely with Overpass, but good practice)
            console.log(f"[yellow]Warning: POI found without 'id' key. Skipping for deduplication: {poi}[/yellow]")

    console.print(f"✅ Deduplication complete. {len(unique_pois)} unique POIs out of {len(pois)} initial ones.")
    return unique_pois


def filter_pois_near_track(track_points_coords, kdtree, pois, max_distance_m=100):
    """
    Keep only POI near trace using a KDTree for efficient proximity search.
    """

    nearby_pois = []
    approx_degrees_per_meter = 1 / 111320.0
    kdtree_radius_degrees = max_distance_m * approx_degrees_per_meter * 1.5

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


def sanitize_gpx_text(data):
    """
    Fix GPX content by replacing unescaped '&' with '&amp;'
    """

    return re.sub(r'&(?!amp;|quot;|lt;|gt;|apos;)', '&amp;', data)


def process_gpx_and_pois(gpx_content, poi_types, max_distance_m, max_bbox_area_sq_deg, lat_divisions, lon_divisions, show_bboxes=False):
    """
    Handles the core logic of parsing GPX, querying POIs, and filtering them.

    Args:
        gpx_content (str): The raw GPX content as a string.
        poi_types (list): List of POI types to search for.
        max_distance_m (int): Max distance for POI filtering.
        max_bbox_area_sq_deg (float): Max area for Overpass query bbox.
        lat_divisions (int): Latitude divisions for bbox subdivision.
        lon_divisions (int): Longitude divisions for bbox subdivision.
        show_bboxes (bool): If True, collect BBoxes used for Overpass queries.

    Returns:
        tuple: (gpx_object, filtered_pois, collected_bboxes), where gpx_object is the parsed gpxpy.GPX object
               and filtered_pois is a list of dictionaries of POIs, and collected_bboxes is a list of BBoxes queried.
    """
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
    console.print(f"Maximum bbox area: {max_bbox_area_sq_deg} sq deg (subdivision factor: {lat_divisions}x{lon_divisions})")

    # Find relevant bboxes
    bboxes = get_relevant_bboxes(
        bounds,
        gpx_kdtree,
        track_points_coords,
        max_bbox_area_sq_deg,
        lat_divisions,
        lon_divisions
    )
    total_overpass_steps = len(bboxes)
    console.print(
        f"Calculated {total_overpass_steps} Overpass query/skip steps.")

    # Display bboxes for debug prupose
    # Initialiser la liste pour la collecte si show_bboxes est True
    collected_bboxes = bboxes if show_bboxes else None

    # Find POIs
    pois = []
    for bbox in rich.progress.track(bboxes, description=f"[cyan]Querying Overpass for {len(poi_types)} POI types[/cyan]"):
        pois.extend(query_overpass(bbox, poi_types, gpx_kdtree))

    console.print(f"Total raw POIs found by Overpass: {len(pois)}")

    # Remove duplicated POIs
    deduplicated_pois = deduplicate_pois_by_id(pois)
    console.print(f"Total unique POIs after deduplication: {len(deduplicated_pois)}")

    # Filter POIs
    filtered_pois = filter_pois_near_track(
        track_points_coords, gpx_kdtree, deduplicated_pois, max_distance_m)
    console.print(f"Total POIs within {max_distance_m}m of track: {len(filtered_pois)}")

    # Retourne également la liste des bboxes collectées
    return gpx, filtered_pois, collected_bboxes
