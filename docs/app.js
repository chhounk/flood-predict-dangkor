// Dangkor Flood Forecast Dashboard
// Vanilla JS + Leaflet — no build step

(function () {
    'use strict';

    // Color ramp — colorblind-safe
    var COLORS = {
        1: { fill: '#d9d9d9', opacity: 0.3 },  // L1 Safe
        2: { fill: '#fee08b', opacity: 0.7 },  // L2 Low
        3: { fill: '#f46d43', opacity: 0.8 },  // L3 Moderate
        4: { fill: '#d73027', opacity: 0.9 }   // L4 High
    };

    var LEVEL_NAMES = { 1: 'Safe', 2: 'Low', 3: 'Moderate', 4: 'High' };

    var currentWindow = '24h';
    var geojsonLayer = null;
    var latestData = null;

    // Initialize map centered on Dangkor
    var map = L.map('map', {
        center: [11.47, 104.85],
        zoom: 13,
        zoomControl: true
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 18
    }).addTo(map);

    // Load data
    loadData();

    // Window selector buttons
    var buttons = document.querySelectorAll('.window-btn');
    buttons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            buttons.forEach(function (b) { b.classList.remove('active'); });
            btn.classList.add('active');
            currentWindow = btn.getAttribute('data-window');
            updateMapStyle();
        });
    });

    function loadData() {
        // Load GeoJSON for map
        fetch('data/latest.geojson')
            .then(function (r) { return r.json(); })
            .then(function (geojson) {
                renderMap(geojson);
            })
            .catch(function (err) {
                console.error('Failed to load GeoJSON:', err);
            });

        // Load JSON for metadata
        fetch('data/latest.json')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                latestData = data;
                updateMeta(data);
                updateSummary(data);
                updateConfidence(data);
            })
            .catch(function (err) {
                console.error('Failed to load JSON:', err);
            });

        // Try loading validation scorecard
        fetch('data/validation_latest.json')
            .then(function (r) {
                if (!r.ok) throw new Error('No validation data');
                return r.json();
            })
            .then(function (data) {
                updateScorecard(data);
            })
            .catch(function () {
                // Expected in v1 — scorecard stays at placeholder text
            });
    }

    function renderMap(geojson) {
        if (geojsonLayer) {
            map.removeLayer(geojsonLayer);
        }

        geojsonLayer = L.geoJSON(geojson, {
            style: function (feature) {
                return getStyle(feature);
            },
            onEachFeature: function (feature, layer) {
                layer.on('click', function () {
                    showPopup(feature, layer);
                });
            }
        }).addTo(map);

        // Fit bounds to data
        if (geojsonLayer.getBounds().isValid()) {
            map.fitBounds(geojsonLayer.getBounds(), { padding: [20, 20] });
        }
    }

    function getStyle(feature) {
        var props = feature.properties;
        var levelKey = 'wp_' + currentWindow + '_level';
        var level = props[levelKey] || props.peak_level || 1;
        var color = COLORS[level] || COLORS[1];

        return {
            fillColor: color.fill,
            fillOpacity: color.opacity,
            color: '#555',
            weight: 0.5,
            opacity: 0.6
        };
    }

    function updateMapStyle() {
        if (geojsonLayer) {
            geojsonLayer.eachLayer(function (layer) {
                if (layer.feature) {
                    layer.setStyle(getStyle(layer.feature));
                }
            });
        }
    }

    function showPopup(feature, layer) {
        var p = feature.properties;
        var pKey = 'wp_' + currentWindow + '_p';
        var lKey = 'wp_' + currentWindow + '_level';
        var prob = p[pKey] || p.peak_probability || 0;
        var level = p[lKey] || p.peak_level || 1;

        var html = '<div style="font-size:13px;line-height:1.6">'
            + '<strong>' + p.grid_id + '</strong>'
            + (p.commune ? ' — ' + p.commune : '') + '<br>'
            + '<strong>Level:</strong> L' + level + ' (' + LEVEL_NAMES[level] + ')<br>'
            + '<strong>Probability:</strong> ' + (prob * 100).toFixed(1) + '%<br>'
            + '<strong>Peak level:</strong> L' + p.peak_level + '<br>'
            + '<strong>Peak prob:</strong> ' + ((p.peak_probability || 0) * 100).toFixed(1) + '%<br>';

        if (p.peak_time) {
            html += '<strong>Peak time:</strong> ' + formatTime(p.peak_time) + '<br>';
        }

        // Show all windows
        html += '<hr style="margin:4px 0;border-color:#555">';
        ['6h', '12h', '24h', '48h', '72h'].forEach(function (w) {
            var wl = p['wp_' + w + '_level'] || 1;
            var wp = p['wp_' + w + '_p'] || 0;
            var marker = (w === currentWindow) ? ' ◄' : '';
            html += w + ': L' + wl + ' (' + (wp * 100).toFixed(0) + '%)' + marker + '<br>';
        });

        html += '</div>';
        layer.bindPopup(html, { maxWidth: 260 }).openPopup();
    }

    function updateMeta(data) {
        var issued = new Date(data.forecast_issued_at);
        document.getElementById('last-updated').textContent =
            'Updated: ' + formatTime(data.forecast_issued_at);

        // Next update in ~6 hours from issued
        var next = new Date(issued.getTime() + 6 * 3600 * 1000);
        var now = new Date();
        var diffH = Math.max(0, (next - now) / 3600000);
        if (diffH > 0) {
            document.getElementById('next-update').textContent =
                'Next update in ~' + Math.ceil(diffH) + 'h';
        }
    }

    function updateSummary(data) {
        var s = data.summary;
        var html = '<strong>Cells:</strong> ' + s.cells_total + '<br>';

        var levels = s.cells_by_peak_level;
        html += 'L4 High: ' + (levels['4'] || 0) + '<br>';
        html += 'L3 Moderate: ' + (levels['3'] || 0) + '<br>';
        html += 'L2 Low: ' + (levels['2'] || 0) + '<br>';
        html += 'L1 Safe: ' + (levels['1'] || 0) + '<br>';

        if (s.peak_risk_time) {
            html += '<strong>Peak risk:</strong> ' + formatTime(s.peak_risk_time);
        }

        document.getElementById('summary-content').innerHTML = html;
    }

    function updateConfidence(data) {
        var gpm = data.regional_signals.gpm_agreement;
        var el = document.getElementById('gpm-value');
        if (gpm !== null && gpm !== undefined) {
            el.textContent = (gpm * 100).toFixed(0) + '%';
            el.style.color = gpm > 0.7 ? '#4fc3f7' : gpm > 0.4 ? '#ff9800' : '#d73027';
        } else {
            el.textContent = 'Unavailable';
            el.style.color = '#888';
        }
    }

    function updateScorecard(data) {
        if (data && data.metrics) {
            var html = '';
            for (var key in data.metrics) {
                html += '<strong>' + key + ':</strong> ' + data.metrics[key] + '<br>';
            }
            document.getElementById('scorecard-content').innerHTML = html;
        }
    }

    function formatTime(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return d.toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
            timeZoneName: 'short'
        });
    }
})();
