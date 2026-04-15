// Dangkor Flood Forecast — SPA controller
// Vanilla JS + Leaflet, hash-based routing

(function () {
    'use strict';

    // ========== Constants ==========
    var COLORS = {
        1: { fill: '#d9d9d9', opacity: 0.35 },
        2: { fill: '#fee08b', opacity: 0.70 },
        3: { fill: '#f46d43', opacity: 0.80 },
        4: { fill: '#d73027', opacity: 0.90 }
    };
    var LEVEL_NAMES = { 1: 'Safe', 2: 'Low', 3: 'Moderate', 4: 'High' };

    // ========== State ==========
    var currentWindow = '24h';
    var geojsonLayer = null;
    var latestData = null;
    var latestGeo = null;
    var map = null;

    // ========== Router ==========
    function route() {
        var hash = window.location.hash || '#/map';
        var view = hash.replace('#/', '') || 'map';
        if (['map', 'dashboard', 'methodology', 'telegram'].indexOf(view) === -1) view = 'map';
        switchView(view);
    }

    function switchView(view) {
        document.querySelectorAll('.view').forEach(function (el) {
            el.classList.toggle('active', el.id === 'view-' + view);
        });
        document.querySelectorAll('.nav-btn').forEach(function (el) {
            el.classList.toggle('active', el.getAttribute('data-view') === view);
        });
        if (view === 'map' && map) {
            // Leaflet needs a size refresh after being hidden/shown
            setTimeout(function () { map.invalidateSize(); }, 50);
        }
    }

    window.addEventListener('hashchange', route);

    // ========== Init ==========
    document.addEventListener('DOMContentLoaded', function () {
        initMap();
        bindWindowButtons();
        route();
        loadData();
    });

    function initMap() {
        map = L.map('map', {
            center: [11.47, 104.85],
            zoom: 13,
            zoomControl: true
        });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18
        }).addTo(map);

        // Hover coordinate badge
        var badge = document.getElementById('coord-badge');
        map.on('mousemove', function (e) {
            badge.textContent =
                e.latlng.lat.toFixed(5) + ', ' + e.latlng.lng.toFixed(5);
        });
        map.on('mouseout', function () { badge.textContent = '—'; });
    }

    function bindWindowButtons() {
        var buttons = document.querySelectorAll('.window-btn');
        buttons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                buttons.forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                currentWindow = btn.getAttribute('data-window');
                updateMapStyle();
                updateSummaryCards();
                updateCommuneRanking();
            });
        });
    }

    // ========== Data ==========
    function loadData() {
        fetch('data/latest.geojson')
            .then(function (r) { return r.json(); })
            .then(function (g) { latestGeo = g; renderMap(g); })
            .catch(function (err) { console.error('GeoJSON load failed:', err); });

        fetch('data/latest.json')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                latestData = d;
                updateHeader(d);
                updateSummaryCards();
                updateCommuneRanking();
                updateSourceStatus(d);
                updateDashboard(d);
            })
            .catch(function (err) { console.error('JSON load failed:', err); });
    }

    // ========== Map ==========
    function renderMap(geojson) {
        if (geojsonLayer) map.removeLayer(geojsonLayer);
        geojsonLayer = L.geoJSON(geojson, {
            style: getStyle,
            onEachFeature: function (feature, layer) {
                layer.on('click', function () { showPopup(feature, layer); });
            }
        }).addTo(map);
        if (geojsonLayer.getBounds().isValid()) {
            map.fitBounds(geojsonLayer.getBounds(), { padding: [20, 20] });
        }
    }

    function getStyle(feature) {
        var p = feature.properties;
        var level = p['wp_' + currentWindow + '_level'] || p.peak_level || 1;
        var c = COLORS[level] || COLORS[1];
        return {
            fillColor: c.fill,
            fillOpacity: c.opacity,
            color: '#2d3a52',
            weight: 0.4,
            opacity: 0.5
        };
    }

    function updateMapStyle() {
        if (!geojsonLayer) return;
        geojsonLayer.eachLayer(function (layer) {
            if (layer.feature) layer.setStyle(getStyle(layer.feature));
        });
    }

    function showPopup(feature, layer) {
        var p = feature.properties;
        var prob = p['wp_' + currentWindow + '_p'] || 0;
        var level = p['wp_' + currentWindow + '_level'] || 1;
        var lat = p.centroid_lat, lon = p.centroid_lon;

        var html = '<strong>' + p.grid_id + '</strong>';
        if (p.commune_name) html += ' · ' + p.commune_name;
        html += '<br>';
        if (lat != null && lon != null) {
            html += '<span style="color:#8a96b3">Center: ' +
                lat.toFixed(5) + ', ' + lon.toFixed(5) + '</span><br>';
        }
        html += '<br>Level (' + currentWindow + '): <strong>L' + level +
            ' ' + LEVEL_NAMES[level] + '</strong><br>';
        html += 'Probability: <strong>' + (prob * 100).toFixed(1) + '%</strong><br>';
        html += '<br>Peak: L' + p.peak_level +
            ' (' + ((p.peak_probability || 0) * 100).toFixed(1) + '%)';
        if (p.peak_time) html += '<br>Peak time: ' + formatTime(p.peak_time);

        html += '<hr>';
        ['6h', '12h', '24h', '48h', '72h'].forEach(function (w) {
            var wl = p['wp_' + w + '_level'] || 1;
            var wp = p['wp_' + w + '_p'] || 0;
            var marker = (w === currentWindow) ? ' ◄' : '';
            html += w + ': L' + wl + ' (' + (wp * 100).toFixed(0) + '%)' + marker + '<br>';
        });
        layer.bindPopup(html, { maxWidth: 280 }).openPopup();
    }

    // ========== Header / Summary cards ==========
    function updateHeader(d) {
        var issued = new Date(d.forecast_issued_at);
        var next = new Date(issued.getTime() + 6 * 3600 * 1000);
        var diffH = Math.max(0, (next - Date.now()) / 3600000);
        document.getElementById('last-updated').textContent =
            'Updated ' + formatTime(d.forecast_issued_at) +
            (diffH > 0 ? ' · next in ~' + Math.ceil(diffH) + 'h' : '');
    }

    function updateSummaryCards() {
        if (!latestData) return;
        // For window-aware counts, recount from cells
        var counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
        var key = 'wp_' + currentWindow + '_level';
        latestData.cells.forEach(function (c) {
            var wp = (c.window_peaks && c.window_peaks[currentWindow]) || {};
            var lev = wp.level || c.peak_level || 1;
            counts[lev] = (counts[lev] || 0) + 1;
        });
        ['1', '2', '3', '4'].forEach(function (k) {
            document.getElementById('count-l' + k).textContent = counts[k] || 0;
        });
    }

    // ========== Commune ranking ==========
    function updateCommuneRanking() {
        if (!latestData) return;
        var el = document.getElementById('commune-ranking');
        var communes = latestData.communes || [];
        if (!communes.length) {
            el.innerHTML = '<span style="color:var(--text-dim)">No data</span>';
            return;
        }
        var html = '';
        communes.forEach(function (c) {
            html += '<div class="commune-row">'
                + '<span class="commune-name">' + c.commune_name + '</span>'
                + '<span class="commune-badge lvl-' + c.peak_level + '">L' + c.peak_level + '</span>'
                + '<span class="commune-pct">' + (c.affected_pct_l3_plus || 0).toFixed(0) + '%</span>'
                + '</div>';
        });
        el.innerHTML = html;
    }

    function updateSourceStatus(d) {
        var gpm = (d.regional_signals || {}).gpm_agreement;
        var dot = document.getElementById('dot-gpm');
        var val = document.getElementById('gpm-value');
        if (gpm != null) {
            val.textContent = (gpm * 100).toFixed(0) + '%';
            dot.className = 'dot ' + (gpm > 0.7 ? 'dot-ok' : 'dot-warn');
        } else {
            val.textContent = 'n/a';
            dot.className = 'dot dot-stub';
        }
    }

    // ========== Dashboard view ==========
    function updateDashboard(d) {
        var s = d.summary || {};
        var cellsTotal = s.cells_total || 0;
        var l3plus = (s.cells_by_peak_level['3'] || 0) + (s.cells_by_peak_level['4'] || 0);
        var peakP = 0;
        d.cells.forEach(function (c) {
            if ((c.peak_probability || 0) > peakP) peakP = c.peak_probability;
        });
        document.getElementById('kpi-cells').textContent = cellsTotal;
        document.getElementById('kpi-l3plus').textContent = l3plus;
        document.getElementById('kpi-peakp').textContent = (peakP * 100).toFixed(1) + '%';
        document.getElementById('kpi-peak-time').textContent =
            s.peak_risk_time ? formatTime(s.peak_risk_time) : '—';

        // Commune table
        var tbody = document.getElementById('commune-table');
        var comms = d.communes || [];
        if (!comms.length) {
            tbody.innerHTML = '<tr><td colspan="5">No data</td></tr>';
        } else {
            tbody.innerHTML = comms.map(function (c) {
                return '<tr>'
                    + '<td>' + c.commune_name + '</td>'
                    + '<td>' + c.cell_count + '</td>'
                    + '<td><span class="commune-badge lvl-' + c.peak_level + '">L' + c.peak_level + '</span></td>'
                    + '<td>' + ((c.peak_probability || 0) * 100).toFixed(1) + '%</td>'
                    + '<td>' + (c.affected_pct_l3_plus || 0).toFixed(1) + '%</td>'
                    + '</tr>';
            }).join('');
        }

        // Window table
        var wbody = document.getElementById('window-table');
        var wp = s.window_peaks || {};
        wbody.innerHTML = ['6h', '12h', '24h', '48h', '72h'].map(function (w) {
            var x = wp[w] || { level_4_cells: 0, level_3_cells: 0 };
            return '<tr><td>' + w + '</td><td>' + x.level_4_cells + '</td><td>' + x.level_3_cells + '</td></tr>';
        }).join('');

        // Meta
        var m = document.getElementById('meta-list');
        var sig = d.regional_signals || {};
        m.innerHTML = ''
            + '<div><span class="k">run_id:</span> ' + d.run_id + '</div>'
            + '<div><span class="k">engine_version:</span> ' + d.engine_version + '</div>'
            + '<div><span class="k">models_used:</span> ' + (d.models_used || []).join(', ') + '</div>'
            + '<div><span class="k">ensemble_size:</span> ' + d.ensemble_size + '</div>'
            + '<div><span class="k">horizon:</span> ' + d.horizon_hours + 'h (' + d.timestep_hours + 'h steps)</div>'
            + '<div><span class="k">gpm_agreement:</span> ' + (sig.gpm_agreement != null ? sig.gpm_agreement.toFixed(2) : 'n/a') + '</div>'
            + '<div><span class="k">jaxa_gsmap:</span> ' + (sig.jaxa_gsmap != null ? sig.jaxa_gsmap.toFixed(2) : 'stub') + '</div>'
            + '<div><span class="k">glofas:</span> ' + (sig.glofas != null ? sig.glofas.toFixed(2) : 'stub') + '</div>';
    }

    // ========== Helpers ==========
    function formatTime(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return d.toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    }
})();
