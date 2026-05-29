// Global state
let uploadedFile = null;
let currentResultId = null;
let elevationChart = null;

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
const poiBreakdownEl = document.getElementById('poi-breakdown');
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

    // Render POI breakdown
    const breakdown = result.poi_breakdown || {};
    const total = Object.values(breakdown).reduce((a, b) => a + b, 0);
    if (total === 0) {
        poiBreakdownEl.innerHTML = '<p class="poi-breakdown-empty">No POIs found near the track.</p>';
    } else {
        const rows = Object.entries(breakdown)
            .sort((a, b) => b[1] - a[1])
            .map(([label, count]) => `
                <div class="poi-breakdown-row">
                    <span class="poi-breakdown-label">${label}</span>
                    <span class="poi-breakdown-count">${count}</span>
                </div>`)
            .join('');
        poiBreakdownEl.innerHTML = `
            <div class="poi-breakdown-total">
                <span>Total</span><span>${total}</span>
            </div>
            ${rows}`;
    }

    // Elevation profile
    renderElevationProfile(result.elevation_profile || null);

    // Roadbook
    renderRoadbook(result.elevation_profile?.pois || []);

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

function slopeColor(slope) {
    if (slope < 0)  return 'rgba(255,255,255,0.9)';
    if (slope < 4)  return '#4ade80';
    if (slope < 8)  return '#facc15';
    if (slope < 12) return '#f97316';
    return '#ef4444';
}

function slopeFillColor(slope) {
    if (slope < 0)  return 'rgba(255,255,255,0.07)';
    if (slope < 4)  return 'rgba(74,222,128,0.28)';
    if (slope < 8)  return 'rgba(250,204,21,0.28)';
    if (slope < 12) return 'rgba(249,115,22,0.28)';
    return 'rgba(239,68,68,0.28)';
}

const POI_COLORS = {
    'Water':             '#60a5fa',
    'Bakery':            '#4ade80',
    'Cafe':              '#f87171',
    'Fuel Station':      '#fb923c',
    'Convenience Store': '#c084fc',
    'Vending Machine':   '#f87171',
};

function stackPois(pois, eleRange) {
    if (!pois.length) return [];
    const step = Math.max(10, eleRange / 15);
    const thresholdKm = 0.5;
    const sorted = pois.slice().sort((a, b) => a.d - b.d);
    const result = [];
    let i = 0;
    while (i < sorted.length) {
        let j = i;
        while (j + 1 < sorted.length && sorted[j + 1].d - sorted[i].d < thresholdKm) j++;
        for (let k = i; k <= j; k++) {
            result.push({ ...sorted[k], stackedEle: sorted[k].ele + (k - i) * step });
        }
        i = j + 1;
    }
    return result;
}

function renderElevationProfile(profileData) {
    const section = document.getElementById('elevation-section');
    if (!profileData) { section.style.display = 'none'; return; }
    section.style.display = 'block';

    const pts  = profileData.points;
    const pois = profileData.pois;

    // Slope per segment (%)
    const slopes = pts.map((p, i) => {
        if (i === pts.length - 1) return 0;
        const dDist = (pts[i + 1].d - p.d) * 1000;
        return dDist > 1 ? ((pts[i + 1].ele - p.ele) / dDist) * 100 : 0;
    });

    // Stack overlapping POI markers
    const eles = pts.map(p => p.ele);
    const eleRange = Math.max(...eles) - Math.min(...eles);
    const stacked = stackPois(pois, eleRange);
    const poiColors = stacked.map(p => POI_COLORS[p.type] || '#94a3b8');

    // Legend — only types actually present
    const presentTypes = [...new Set(stacked.map(p => p.type))];
    document.getElementById('elevation-legend').innerHTML = presentTypes.map(t =>
        `<span class="poi-legend-item">
            <span class="poi-legend-dot" style="background:${POI_COLORS[t] || '#94a3b8'}"></span>
            ${t}
        </span>`
    ).join('');

    const maxDist = pts[pts.length - 1].d;

    // Inline plugin: fills each segment with the slope colour
    const fillBySlopePlugin = {
        id: 'fillBySlope',
        beforeDatasetsDraw(chart) {
            const { ctx: c, chartArea, scales } = chart;
            const meta = chart.getDatasetMeta(0);
            if (!meta?.data.length) return;
            c.save();
            c.beginPath();
            c.rect(chartArea.left, chartArea.top, chartArea.width, chartArea.height);
            c.clip();
            for (let i = 0; i < meta.data.length - 1; i++) {
                const p0 = meta.data[i];
                const p1 = meta.data[i + 1];
                c.beginPath();
                c.moveTo(p0.x, chartArea.bottom);
                c.lineTo(p0.x, p0.y);
                c.lineTo(p1.x, p1.y);
                c.lineTo(p1.x, chartArea.bottom);
                c.closePath();
                c.fillStyle = slopeFillColor(slopes[i]);
                c.fill();
            }
            c.restore();
        },
    };

    if (elevationChart) elevationChart.destroy();
    document.getElementById('elevation-reset').onclick = () => elevationChart?.resetZoom();
    const ctx = document.getElementById('elevation-canvas').getContext('2d');

    elevationChart = new Chart(ctx, {
        plugins: [fillBySlopePlugin],
        data: {
            datasets: [
                {
                    type: 'line',
                    label: 'Elevation',
                    data: pts.map(p => ({ x: p.d, y: p.ele })),
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.2,
                    fill: false,
                    segment: { borderColor: c => slopeColor(slopes[c.p0DataIndex]) },
                    order: 2,
                },
                {
                    type: 'scatter',
                    label: 'POIs',
                    data: stacked.map(p => ({ x: p.d, y: p.stackedEle, name: p.name, type: p.type, origEle: p.ele })),
                    pointStyle: 'circle',
                    pointRadius: 6,
                    pointHoverRadius: 9,
                    backgroundColor: poiColors,
                    borderColor: 'rgba(255,255,255,0.8)',
                    borderWidth: 1.5,
                    order: 1,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false, axis: 'x' },
            plugins: {
                legend: { display: false },
                zoom: {
                    limits: {
                        x: { min: 0, max: maxDist, minRange: 0.5 },
                    },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: 'x',
                    },
                    pan: {
                        enabled: true,
                        mode: 'x',
                    },
                },
                tooltip: {
                    callbacks: {
                        title: items => `${Number(items[0].parsed.x).toFixed(1)} km`,
                        label: item => {
                            if (item.datasetIndex === 0) {
                                const s = slopes[item.dataIndex];
                                return `${item.parsed.y} m  |  pente : ${s.toFixed(1)} %`;
                            }
                            const poi = stacked[item.dataIndex];
                            const name = poi.name || '(sans nom)';
                            return `${poi.type} — ${name}  (${poi.origEle} m)`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    type: 'linear',
                    min: 0,
                    max: maxDist,
                    title: { display: true, text: 'Distance (km)', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.07)' },
                },
                y: {
                    title: { display: true, text: 'Élévation (m)', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.07)' },
                },
            },
        },
    });
}

function groupPoisByDistance(pois, thresholdKm = 0.5) {
    const sorted = pois.slice().sort((a, b) => a.d - b.d);
    const groups = [];
    let i = 0;
    while (i < sorted.length) {
        const group = [sorted[i]];
        let j = i + 1;
        while (j < sorted.length && sorted[j].d - sorted[i].d < thresholdKm) {
            group.push(sorted[j]);
            j++;
        }
        groups.push(group);
        i = j;
    }
    return groups;
}

function renderRoadbook(pois) {
    const section = document.getElementById('roadbook-section');
    const tbody   = document.getElementById('roadbook-body');

    if (!pois || !pois.length) { section.style.display = 'none'; return; }

    section.style.display = 'block';
    const groups = groupPoisByDistance(pois);

    tbody.innerHTML = groups.map(group => {
        const first = group[0];

        const cities = [...new Set(group.map(p => p.city).filter(Boolean))];
        const cityHtml = cities.length
            ? `<span class="roadbook-city">${cities.join(', ')}</span>`
            : '';

        const typesCells = group.map(poi => {
            const color = POI_COLORS[poi.type] || '#94a3b8';
            return `<span class="roadbook-type-entry">
                        <span class="poi-legend-dot" style="background:${color}"></span>${poi.type}
                    </span>`;
        }).join('');

        const namesCells = group.map(poi =>
            poi.name || '<em>sans nom</em>'
        ).join('<br>');

        return `<tr>
            <td class="roadbook-dist">${first.d.toFixed(1)} km${cityHtml}</td>
            <td class="roadbook-ele">${first.ele} m</td>
            <td class="roadbook-type">${typesCells}</td>
            <td class="roadbook-name">${namesCells}</td>
        </tr>`;
    }).join('');
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

// Max distance slider live value
document.getElementById('max-distance').addEventListener('input', (e) => {
    document.getElementById('max-distance-value').textContent = e.target.value;
});

// Add smooth animations on page load
document.addEventListener('DOMContentLoaded', () => {
    // Animate cards on load
    const cards = document.querySelectorAll('.card');
    cards.forEach((card, index) => {
        card.style.animationDelay = `${index * 0.1}s`;
    });
});
