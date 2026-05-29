// Global state
let uploadedFile = null;
let currentResultId = null;

// DOM Elements
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('gpx-file-input');
const fileInfo = document.getElementById('file-info');
const fileName = document.getElementById('file-name');
const removeFileBtn = document.getElementById('remove-file');
const configForm = document.getElementById('config-form');
const processBtn = document.getElementById('process-btn');
const uploadSection = document.getElementById('upload-section');
const resultsSection = document.getElementById('results-section');
const loadingOverlay = document.getElementById('loading-overlay');
const mapFrame = document.getElementById('map-frame');
const poiCountEl = document.getElementById('poi-count');
const downloadGpxBtn = document.getElementById('download-gpx');
const downloadHtmlBtn = document.getElementById('download-html');
const newSearchBtn = document.getElementById('new-search');

// File Upload Handling
dropZone.addEventListener('click', () => {
    if (!uploadedFile) {
        fileInput.click();
    }
});

dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileSelect(files[0]);
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

removeFileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    resetFileUpload();
});

function handleFileSelect(file) {
    if (!file.name.endsWith('.gpx')) {
        alert('Please select a GPX file');
        return;
    }

    uploadedFile = file;
    fileName.textContent = file.name;

    // Show file info and hide drop zone content
    document.querySelector('.drop-zone-content').style.display = 'none';
    fileInfo.style.display = 'flex';

    // Show configuration form
    configForm.style.display = 'block';

    // Smooth scroll to form
    setTimeout(() => {
        configForm.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 100);
}

function resetFileUpload() {
    uploadedFile = null;
    fileInput.value = '';
    fileName.textContent = '';

    document.querySelector('.drop-zone-content').style.display = 'block';
    fileInfo.style.display = 'none';
    configForm.style.display = 'none';
}

// Form Submission
configForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    if (!uploadedFile) {
        alert('Please select a GPX file first');
        return;
    }

    hideError();
    showLoading(true);
    setButtonLoading(processBtn, true);

    // Generate unique session ID
    const sessionId = generateSessionId();
    let eventSource = null;

    try {
        // Prepare form data
        const formData = new FormData();
        formData.append('gpx_file', uploadedFile);
        formData.append('session_id', sessionId);

        // Get selected POI types
        const selectedPois = Array.from(
            document.querySelectorAll('input[name="poi_types"]:checked')
        ).map(cb => cb.value);

        selectedPois.forEach(poi => {
            formData.append('poi_types[]', poi);
        });

        // Get other parameters
        formData.append('max_distance', document.getElementById('max-distance').value);
        formData.append('show_bboxes', document.getElementById('show-bboxes').checked);

        // Set up EventSource for progress updates
        eventSource = new EventSource(`/progress/${sessionId}`);

        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.complete) {
                eventSource.close();

                if (data.success) {
                    displayResults(data);
                } else {
                    showLoading(false);
                    setButtonLoading(processBtn, false);
                    showError(data.error || 'Processing failed');
                }
            } else {
                updateProgress(data);
            }
        };

        eventSource.onerror = () => {
            eventSource.close();
            showLoading(false);
            setButtonLoading(processBtn, false);
            showError('Connection lost. Please try again.');
        };

        // Send upload request (processing happens in background)
        const response = await fetch('/upload', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Upload failed');
        }

        // Upload successful, progress updates will come via EventSource

    } catch (error) {
        console.error('Error:', error);
        if (eventSource) eventSource.close();
        showLoading(false);
        setButtonLoading(processBtn, false);
        showError(error.message || 'An unexpected error occurred.');
    }
});

function displayResults(result) {
    // Hide loading state
    showLoading(false);
    setButtonLoading(processBtn, false);

    currentResultId = result.result_id;

    // Update POI count
    poiCountEl.textContent = result.poi_count;

    // Embed map
    const mapDoc = mapFrame.contentDocument || mapFrame.contentWindow.document;
    mapDoc.open();
    mapDoc.write(result.map_html);
    mapDoc.close();

    // Hide upload section and show results
    uploadSection.style.display = 'none';
    resultsSection.style.display = 'block';

    // Scroll to results
    resultsSection.scrollIntoView({ behavior: 'smooth' });
}

// Download Handlers
downloadGpxBtn.addEventListener('click', () => {
    if (currentResultId) {
        window.location.href = `/download/${currentResultId}/gpx`;
    }
});

downloadHtmlBtn.addEventListener('click', () => {
    if (currentResultId) {
        window.location.href = `/download/${currentResultId}/html`;
    }
});

// New Search
newSearchBtn.addEventListener('click', () => {
    // Reset everything
    resetFileUpload();
    currentResultId = null;

    // Show upload section and hide results
    uploadSection.style.display = 'block';
    resultsSection.style.display = 'none';

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
});

// Utility Functions
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

function updateProgress(data) {
    const progressContainer = document.getElementById('progress-container');
    const progressFill = document.getElementById('progress-fill');
    const progressStage = document.getElementById('progress-stage');
    const progressPercentage = document.getElementById('progress-percentage');
    const progressPoiCount = document.getElementById('progress-poi-count');

    // Show progress container and hide initial loading text
    progressContainer.style.display = 'block';
    document.querySelector('.loading-text').style.display = 'none';
    document.querySelector('.loading-subtext').style.display = 'none';

    // Calculate percentage
    const percentage = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;

    // Update progress bar
    progressFill.style.width = percentage + '%';

    // Update stage text
    progressStage.textContent = data.stage || 'Processing...';

    // Update percentage
    progressPercentage.textContent = percentage + '%';

    // Update POI count
    progressPoiCount.textContent = data.poi_count || 0;
}

function showError(message) {
    const errorDiv = document.getElementById('error-message');
    const errorText = document.getElementById('error-text');
    errorText.textContent = message;
    errorDiv.style.display = 'block';
    errorDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    resetFileUpload();
}

function hideError() {
    document.getElementById('error-message').style.display = 'none';
}

function showLoading(show) {
    const loadingOverlay = document.getElementById('loading-overlay');
    const progressContainer = document.getElementById('progress-container');

    loadingOverlay.style.display = show ? 'flex' : 'none';

    if (!show) {
        // Reset progress UI when hiding
        progressContainer.style.display = 'none';
        document.querySelector('.loading-text').style.display = 'block';
        document.querySelector('.loading-subtext').style.display = 'block';
        document.getElementById('progress-fill').style.width = '0%';
        document.getElementById('progress-stage').textContent = 'Initializing...';
        document.getElementById('progress-percentage').textContent = '0%';
        document.getElementById('progress-poi-count').textContent = '0';
    }
}

function setButtonLoading(button, loading) {
    const btnText = button.querySelector('.btn-text');
    const btnLoader = button.querySelector('.btn-loader');

    if (loading) {
        btnText.style.display = 'none';
        btnLoader.style.display = 'block';
        button.disabled = true;
    } else {
        btnText.style.display = 'block';
        btnLoader.style.display = 'none';
        button.disabled = false;
    }
}

// Add smooth animations on page load
document.addEventListener('DOMContentLoaded', () => {
    // Animate cards on load
    const cards = document.querySelectorAll('.card');
    cards.forEach((card, index) => {
        card.style.animationDelay = `${index * 0.1}s`;
    });
});
