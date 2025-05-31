import argparse
import sys
import os
import rich.console

import thirsty.core

console = rich.console.Console()


def main():
    parser = argparse.ArgumentParser(
        description="Fetch points of interest (POIs) near a GPX track and add them to a new GPX file or display them on an HTML map."
    )
    parser.add_argument("gpx_input", help="Path to the input GPX file.")
    parser.add_argument(
        "gpx_output", help="Path to the output GPX file with POIs.")
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate an HTML map displaying the GPX track and POIs.",
    )
    parser.add_argument(
        "--poi-type",
        "-p",
        action="append",
        choices=list(thirsty.core.AMENITIES.keys()),
        default=list(thirsty.core.AMENITIES.keys()),
        help=f"Type of POI to search for. Choose from: {', '.join(thirsty.core.AMENITIES.keys(
        ))}. Can be specified multiple times. Default: ALL available types."
    )
    parser.add_argument(
        "--max-distance",
        "-d",
        type=int,
        default=100,
        help="Maximum distance in meters from the GPX track to consider a POI. Default: 100.",
    )
    parser.add_argument(
        "--max-bbox-area",
        type=float,
        default=0.5,
        help="Maximum area in square degrees for an Overpass query bounding box before subdivision. Default: 0.5.",
    )
    parser.add_argument(
        "--lat-divisions",
        type=int,
        default=2,
        help="Number of latitude divisions when subdividing a large bounding box. Default: 2.",
    )
    parser.add_argument(
        "--lon-divisions",
        type=int,
        default=2,
        help="Number of longitude divisions when subdividing a large bounding box. Default: 2.",
    )
    parser.add_argument(  # Nouvel argument pour afficher les BBoxes
        "--show-bboxes",
        action="store_true",
        help="Display the Overpass query bounding boxes as semi-transparent rectangles on the HTML map.",
    )

    args = parser.parse_args()

    gpx_input_path = args.gpx_input
    gpx_output_path = args.gpx_output

    if not os.path.exists(gpx_input_path):
        console.print(f"[bold red]Error: Input GPX file not found at {
                      gpx_input_path}[/bold red]")
        sys.exit(1)

    try:
        with open(gpx_input_path, 'r', encoding='utf-8') as gpx_file:
            gpx_content = gpx_file.read()
    except Exception as e:
        console.print(f"[bold red]Error reading GPX file: {e}[/bold red]")
        sys.exit(1)

    try:
        # Récupérer les bboxes collectées
        gpx_original, filtered_pois, collected_bboxes = thirsty.core.process_gpx_and_pois(
            gpx_content,
            args.poi_type,
            args.max_distance,
            args.max_bbox_area,
            args.lat_divisions,
            args.lon_divisions,
            args.show_bboxes  # Passer la valeur de show_bboxes
        )
    except Exception as e:
        console.print(
            f"[bold red]An error occurred during POI processing: {e}[/bold red]")
        sys.exit(1)

    # Logique pour écrire le fichier GPX de sortie
    if filtered_pois:
        gpx_with_pois = thirsty.core.add_waypoints_to_gpx(
            gpx_original, filtered_pois)
        try:
            with open(gpx_output_path, 'w', encoding='utf-8') as output_gpx_file:
                output_gpx_file.write(gpx_with_pois.to_xml())
            console.print(f"✅ Successfully wrote GPX with POIs to: {
                          gpx_output_path}")
        except Exception as e:
            console.print(
                f"[bold red]Error writing output GPX file: {e}[/bold red]")
            sys.exit(1)
    else:
        console.print(
            "[yellow]No POIs found near the track. Output GPX file will be identical to input.[/yellow]")
        try:
            # Si aucun POI n'est trouvé, copier simplement l'entrée vers la sortie
            with open(gpx_input_path, 'r', encoding='utf-8') as src_gpx_file:
                with open(gpx_output_path, 'w', encoding='utf-8') as dest_gpx_file:
                    dest_gpx_file.write(src_gpx_file.read())
            console.print(f"✅ Copied input GPX to output as no POIs were added: {
                          gpx_output_path}")
        except Exception as e:
            console.print(
                f"[bold red]Error copying input GPX to output: {e}[/bold red]")
            sys.exit(1)

    # Logique pour générer la carte HTML
    if args.html:
        console.print("Generating HTML map...")
        try:
            map_html = thirsty.core.display_gpx_on_map(
                gpx_original, filtered_pois, collected_bboxes)  # Passer collected_bboxes
            html_output_path = os.path.splitext(gpx_output_path)[0] + ".html"
            map_html.save(html_output_path)
            console.print(f"✅ Successfully generated HTML map: {
                          html_output_path}")
        except Exception as e:
            console.print(
                f"[bold red]Error generating HTML map: {e}[/bold red]")


if __name__ == "__main__":
    main()
