"""
Flask web application for Thirsty GPX POI Finder.
Provides a web interface for uploading GPX files and finding POIs.
"""

import os
import io
import json
import uuid
import threading
from queue import Queue
from flask import Flask, render_template, request, jsonify, send_file, Response
from werkzeug.utils import secure_filename
import thirsty.core

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Store processing results temporarily (in production, use Redis or database)
processing_results = {}

# Store progress queues for active sessions
progress_queues = {}


@app.route('/')
def index():
    """Render the main web interface."""
    return render_template('index.html', amenities=thirsty.core.AMENITIES)


@app.route('/progress/<session_id>')
def progress(session_id):
    """Stream progress updates via Server-Sent Events."""
    def generate():
        # Create a queue for this session if it doesn't exist
        if session_id not in progress_queues:
            progress_queues[session_id] = Queue()
        
        queue = progress_queues[session_id]
        
        try:
            while True:
                # Get progress update from queue (blocking)
                progress_data = queue.get()
                
                # Check if processing is complete
                if progress_data.get('complete'):
                    yield f"data: {json.dumps(progress_data)}\n\n"
                    break
                
                # Send progress update
                yield f"data: {json.dumps(progress_data)}\n\n"
        finally:
            # Clean up queue when client disconnects
            if session_id in progress_queues:
                del progress_queues[session_id]
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle GPX file upload and processing."""
    try:
        # Check if file was uploaded
        if 'gpx_file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['gpx_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.gpx'):
            return jsonify({'error': 'File must be a GPX file'}), 400
        
        # Read GPX content
        gpx_content = file.read().decode('utf-8')
        
        # Get parameters from request
        poi_types = request.form.getlist('poi_types[]')
        if not poi_types:
            poi_types = list(thirsty.core.AMENITIES.keys())
        
        max_distance = int(request.form.get('max_distance', 100))
        max_bbox_area = float(request.form.get('max_bbox_area', 0.5))
        lat_divisions = int(request.form.get('lat_divisions', 2))
        lon_divisions = int(request.form.get('lon_divisions', 2))
        show_bboxes = request.form.get('show_bboxes', 'false') == 'true'
        
        # Generate session ID
        session_id = request.form.get('session_id', str(uuid.uuid4()))
        
        # Create progress queue for this session if it doesn't exist
        if session_id not in progress_queues:
            progress_queues[session_id] = Queue()
        
        # Generate output filenames
        original_filename = secure_filename(file.filename)
        base_name = os.path.splitext(original_filename)[0]
        
        def process_in_background():
            """Background processing function."""
            try:
                def progress_callback(progress_data):
                    """Callback to send progress updates."""
                    if session_id in progress_queues:
                        progress_queues[session_id].put(progress_data)
                
                # Process GPX and find POIs with progress callback
                gpx_original, filtered_pois, collected_bboxes = thirsty.core.process_gpx_and_pois(
                    gpx_content,
                    poi_types,
                    max_distance,
                    max_bbox_area,
                    lat_divisions,
                    lon_divisions,
                    show_bboxes,
                    progress_callback=progress_callback
                )
                
                output_gpx_filename = f"{base_name}_with_pois.gpx"
                output_html_filename = f"{base_name}_map.html"
                
                # Save enhanced GPX
                gpx_with_pois = thirsty.core.add_waypoints_to_gpx(gpx_original, filtered_pois)
                output_gpx_path = os.path.join(app.config['OUTPUT_FOLDER'], output_gpx_filename)
                with open(output_gpx_path, 'w', encoding='utf-8') as f:
                    f.write(gpx_with_pois.to_xml())
                
                # Generate and save HTML map
                map_html = thirsty.core.display_gpx_on_map(
                    gpx_original, 
                    filtered_pois, 
                    collected_bboxes if show_bboxes else None
                )
                output_html_path = os.path.join(app.config['OUTPUT_FOLDER'], output_html_filename)
                map_html.save(output_html_path)
                
                # Get map HTML content for embedding
                with open(output_html_path, 'r', encoding='utf-8') as f:
                    map_html_content = f.read()
                
                # Store results
                result_id = base_name
                processing_results[result_id] = {
                    'gpx_path': output_gpx_path,
                    'html_path': output_html_path,
                    'poi_count': len(filtered_pois),
                    'map_html': map_html_content,
                    'gpx_filename': output_gpx_filename,
                    'html_filename': output_html_filename
                }
                
                # Send completion message
                if session_id in progress_queues:
                    progress_queues[session_id].put({
                        'complete': True,
                        'success': True,
                        'result_id': result_id,
                        'poi_count': len(filtered_pois),
                        'map_html': map_html_content,
                        'gpx_filename': output_gpx_filename,
                        'html_filename': output_html_filename
                    })
                
            except Exception as e:
                # Send error message
                if session_id in progress_queues:
                    progress_queues[session_id].put({
                        'complete': True,
                        'success': False,
                        'error': str(e)
                    })
        
        # Start background processing
        thread = threading.Thread(target=process_in_background)
        thread.daemon = True
        thread.start()
        
        # Return session ID immediately
        return jsonify({
            'session_id': session_id
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/download/<result_id>/<file_type>')
def download_file(result_id, file_type):
    """Download processed files."""
    if result_id not in processing_results:
        return jsonify({'error': 'Result not found'}), 404
    
    result = processing_results[result_id]
    
    if file_type == 'gpx':
        return send_file(
            result['gpx_path'],
            as_attachment=True,
            download_name=os.path.basename(result['gpx_path'])
        )
    elif file_type == 'html':
        return send_file(
            result['html_path'],
            as_attachment=True,
            download_name=os.path.basename(result['html_path'])
        )
    else:
        return jsonify({'error': 'Invalid file type'}), 400


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
