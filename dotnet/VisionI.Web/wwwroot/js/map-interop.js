// VISION-I // MAP INTEROP // v4 -- jamming + airspace + satellite + narrative layers
// Queue all Blazor calls before map style loads, then flush.

window.viMap = (function () {
    'use strict';

    const mapCfg = window.VisionIMapConfig || {};
    const defaultStyleUrl = typeof mapCfg.styleUrl === 'string' && mapCfg.styleUrl.length
        ? mapCfg.styleUrl
        : 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
    const defaultCenter = Array.isArray(mapCfg.initialCenter) && mapCfg.initialCenter.length === 2
        ? mapCfg.initialCenter
        : [10, 25];
    const defaultZoom = typeof mapCfg.initialZoom === 'number' ? mapCfg.initialZoom : 2;

    let _map = null, _dotNetRef = null, _ready = false;
    let _focusPopup = null;
    let _resizeObserver = null;
    let _queue = [];

    let _showFlights   = true,  _showVessels  = true;
    let _showEvents    = true,  _showHeat     = true;
    let _showJamming   = false, _showAirspace = false;
    let _showSatellite = false, _showNarrative = false;
    let _showEscalation = false;

    const _targets  = {};
    const _current  = {};
    const _history  = {};
    let   _staticAssetFeatures = null;
    let   _rafId    = null;
    let   _viewportDebounce = null;
    let   _wsMap    = null;
    let   _wsMapContainerId = null;
    let   _wsDotNetRef = null;

    function _whenReady(fn) {
        if (_ready) { try { fn(); } catch(e) { console.warn('[viMap]', e); } }
        else        { _queue.push(fn); }
    }

    function _flush() {
        _ready = true;
        _queue.forEach(fn => { try { fn(); } catch(e) { console.warn('[viMap]', e); } });
        _queue = [];
        _startAnimLoop();
    }

    function emptyGJ() { return { type: 'FeatureCollection', features: [] }; }

    function _bboxPolygon(latMin, lonMin, latMax, lonMax, props) {
        if (![latMin, lonMin, latMax, lonMax].every(Number.isFinite)) return null;
        return {
            type: 'Feature',
            geometry: {
                type: 'Polygon',
                coordinates: [[[lonMin,latMin],[lonMax,latMin],[lonMax,latMax],[lonMin,latMax],[lonMin,latMin]]]
            },
            properties: props || {}
        };
    }

    function _aircraftSVG(color) {
        return `<svg viewBox="0 0 32 32" width="32" height="32" xmlns="http://www.w3.org/2000/svg">
            <path d="M16 2 L17.5 14 L27 19 L27 21 L17.5 17 L17 28 L19 29 L19 30 L16 29 L13 30 L13 29 L15 28 L14.5 17 L5 21 L5 19 L14.5 14 Z"
                fill="${color}" opacity="0.95"/>
        </svg>`;
    }

    function _vesselSVG(color) {
        return `<svg viewBox="0 0 24 24" width="24" height="24" xmlns="http://www.w3.org/2000/svg">
            <polygon points="12,2 22,22 12,16 2,22" fill="${color}" opacity="0.92"/>
        </svg>`;
    }

    function _loadImageFromSVG(id, svg, w, h) {
        return new Promise(resolve => {
            if (_map.hasImage(id)) { resolve(); return; }
            const img = new Image(w, h);
            img.onload = () => { _map.addImage(id, img, { sdf: false }); resolve(); };
            img.onerror = resolve;
            img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
        });
    }

    function _loadImageFromSVGForMap(map, id, svg, w, h) {
        return new Promise(resolve => {
            if (!map || map.hasImage(id)) { resolve(); return; }
            const img = new Image(w, h);
            img.onload = () => {
                try { if (!map.hasImage(id)) map.addImage(id, img, { sdf: false }); } catch (_) {}
                resolve();
            };
            img.onerror = resolve;
            img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
        });
    }

    function _lerp(a, b, t) { return a + (b - a) * t; }
    function _lerpAngle(a, b, t) {
        let d = ((b - a + 540) % 360) - 180;
        return (a + d * t + 360) % 360;
    }

    function _startAnimLoop() {
        if (_rafId) return;
        const SPEED = 0.08;
        function tick() {
            let dirty = false;
            for (const id of Object.keys(_targets)) {
                const tgt = _targets[id];
                const cur = _current[id] || { lat: tgt.lat, lon: tgt.lon, heading: tgt.heading };
                _current[id] = {
                    lat:     _lerp(cur.lat,     tgt.lat,     SPEED),
                    lon:     _lerp(cur.lon,     tgt.lon,     SPEED),
                    heading: _lerpAngle(cur.heading, tgt.heading, SPEED)
                };
                dirty = true;
            }
            if (dirty) _pushCurrentToMap();
            _rafId = requestAnimationFrame(tick);
        }
        _rafId = requestAnimationFrame(tick);
    }

    function _pushCurrentToMap() {
        if (!_map || !_map.getSource('assets-source')) return;
        if (_staticAssetFeatures) {
            _map.getSource('assets-source').setData({ type: 'FeatureCollection', features: _staticAssetFeatures });
            return;
        }
        const features = [];
        for (const [id, cur] of Object.entries(_current)) {
            const tgt = _targets[id];
            if (!tgt) continue;
            if (tgt.type === 'aircraft' && !_showFlights) continue;
            if (tgt.type === 'vessel'   && !_showVessels) continue;
            features.push({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [cur.lon, cur.lat] },
                properties: { id, icon: tgt.icon, heading: cur.heading, label: tgt.label, type: tgt.type, speed: tgt.speed, color: tgt.color }
            });
        }
        _map.getSource('assets-source').setData({ type: 'FeatureCollection', features });
        _rebuildTrails();
    }

    function _rebuildTrails() {
        if (!_map.getSource('asset-trails')) return;
        const features = [];
        for (const [id, hist] of Object.entries(_history)) {
            if (hist.length > 1) {
                const tgt = _targets[id];
                features.push({
                    type: 'Feature',
                    geometry: { type: 'LineString', coordinates: hist },
                    properties: { id, color: tgt?.color || '#475569' }
                });
            }
        }
        _map.getSource('asset-trails').setData({ type: 'FeatureCollection', features });
    }

    // Tell Blazor the current viewport so it can fetch only the assets in view.
    function _emitViewport() {
        if (!_map || !_dotNetRef) return;
        try {
            const b = _map.getBounds();
            _dotNetRef.invokeMethodAsync(
                'OnViewportChanged',
                b.getSouth(), b.getWest(), b.getNorth(), b.getEast(), _map.getZoom()
            ).catch(() => {});
        } catch (_) {}
    }

    function _scheduleViewportEmit() {
        if (_viewportDebounce) clearTimeout(_viewportDebounce);
        _viewportDebounce = setTimeout(_emitViewport, 400);
    }

    function resize() {
        if (!_map) return false;
        try {
            _map.resize();
            return true;
        } catch (_) {
            return false;
        }
    }

    async function initMap(dotNetRef) {
        _dotNetRef = dotNetRef;
        _ready = false;
        _queue = [];
        _focusPopup = null;

        if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
        if (_map)   { try { _map.remove(); } catch(_) {} _map = null; }
        if (_resizeObserver) { try { _resizeObserver.disconnect(); } catch (_) {} _resizeObserver = null; }

        const container = document.getElementById('vi-map');
        if (!container) { console.error('[viMap] #vi-map not found'); return false; }

        _map = new maplibregl.Map({
            container,
            style: defaultStyleUrl,
            center: defaultCenter, zoom: defaultZoom,
            attributionControl: false,
            renderWorldCopies: false,
            preserveDrawingBuffer: true   // required for exportImage()
        });
        _map.addControl(new maplibregl.NavigationControl({ showCompass: true, showZoom: true }), 'bottom-right');
        _map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 100 }), 'bottom-left');

        if (typeof ResizeObserver !== 'undefined') {
            _resizeObserver = new ResizeObserver(() => { resize(); });
            _resizeObserver.observe(container);
        }

        _map.on('load', async () => {
            resize();
            _map.addSource('narrative-heat', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'narrative-heatmap', type: 'heatmap', source: 'narrative-heat',
                layout: { visibility: 'none' },
                paint: {
                    'heatmap-weight': ['get', 'weight'],
                    'heatmap-intensity': 1.2,
                    'heatmap-radius': 60,
                    'heatmap-opacity': 0.65,
                    'heatmap-color': [
                        'interpolate', ['linear'], ['heatmap-density'],
                        0, 'rgba(0,0,0,0)',
                        0.3, 'rgba(167,139,250,0.5)',
                        0.7, 'rgba(210,153,34,0.8)',
                        1,   'rgba(248,81,73,1)'
                    ]
                }
            });
            _map.addSource('escalation-source', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'escalation-fill', type: 'circle', source: 'escalation-source',
                layout: { visibility: 'none' },
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['get', 'score'], 0, 8, 1, 44],
                    'circle-color': ['interpolate', ['linear'], ['get', 'score'],
                        0, 'rgba(63,185,80,0.35)', 0.4, 'rgba(240,180,41,0.55)',
                        0.7, 'rgba(240,110,41,0.7)', 1, 'rgba(248,81,73,0.85)'],
                    'circle-blur': 0.7,
                    'circle-stroke-width': 1,
                    'circle-stroke-color': 'rgba(248,81,73,0.9)',
                    'circle-opacity': 0.75
                }
            });
            _map.addSource('jamming-source', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'jamming-fill', type: 'fill', source: 'jamming-source',
                layout: { visibility: 'none' },
                paint: {
                    'fill-color': '#f85149',
                    'fill-opacity': ['interpolate', ['linear'], ['get', 'intensity'], 0, 0.08, 1, 0.45]
                }
            });
            _map.addLayer({
                id: 'jamming-outline', type: 'line', source: 'jamming-source',
                layout: { visibility: 'none' },
                paint: { 'line-color': '#f85149', 'line-width': 0.8, 'line-opacity': 0.5 }
            });
            _map.addSource('airspace-source', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'airspace-fill', type: 'fill', source: 'airspace-source',
                layout: { visibility: 'none' },
                paint: {
                    'fill-color': ['case', ['==', ['get', 'closure_type'], 'NFZ'], '#f85149', '#d29922'],
                    'fill-opacity': 0.12
                }
            });
            _map.addLayer({
                id: 'airspace-outline', type: 'line', source: 'airspace-source',
                layout: { visibility: 'none' },
                paint: {
                    'line-color': ['case', ['==', ['get', 'closure_type'], 'NFZ'], '#f85149', '#d29922'],
                    'line-width': 1.5,
                    'line-dasharray': [4, 2],
                    'line-opacity': 0.8
                }
            });
            _map.addSource('satellite-source', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'satellite-track', type: 'line', source: 'satellite-source',
                layout: { visibility: 'none', 'line-join': 'round' },
                paint: { 'line-color': '#3fb950', 'line-width': 1.2, 'line-opacity': 0.7, 'line-dasharray': [3, 5] }
            });
            _map.addLayer({
                id: 'satellite-pts', type: 'circle', source: 'satellite-source',
                filter: ['==', ['geometry-type'], 'Point'],
                layout: { visibility: 'none' },
                paint: { 'circle-color': '#3fb950', 'circle-radius': 4, 'circle-opacity': 0.8 }
            });
            _map.addSource('events-heat', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'heatmap', type: 'heatmap', source: 'events-heat',
                paint: {
                    'heatmap-weight': ['coalesce', ['get', 'weight'], 1],
                    'heatmap-intensity': 1.5,
                    'heatmap-radius': 30,
                    'heatmap-opacity': 0.7,
                    'heatmap-color': [
                        'interpolate', ['linear'], ['heatmap-density'],
                        0, 'rgba(0,0,0,0)',
                        0.3, 'rgba(59,130,246,0.6)',
                        0.6, 'rgba(245,158,11,0.8)',
                        1,   'rgba(248,81,73,1)'
                    ]
                }
            });
            _map.addSource('events-cluster', { type: 'geojson', data: emptyGJ(), cluster: true, clusterMaxZoom: 12, clusterRadius: 40 });
            _map.addLayer({
                id: 'evt-clusters', type: 'circle', source: 'events-cluster',
                filter: ['has', 'point_count'],
                paint: { 'circle-color': '#f85149', 'circle-radius': ['step', ['get', 'point_count'], 10, 10, 16, 50, 22], 'circle-stroke-width': 1, 'circle-stroke-color': 'rgba(248,81,73,0.4)' }
            });
            _map.addLayer({
                id: 'evt-cluster-count', type: 'symbol', source: 'events-cluster',
                filter: ['has', 'point_count'],
                layout: { 'text-field': '{point_count_abbreviated}', 'text-size': 10, 'text-font': ['Open Sans Bold','Arial Unicode MS Bold'] },
                paint: { 'text-color': '#fff' }
            });
            _map.addLayer({
                id: 'evt-points-glow', type: 'circle', source: 'events-cluster',
                filter: ['!', ['has', 'point_count']],
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['coalesce', ['get', 'riskScore'], 0.35], 0, 8, 1, 18],
                    'circle-color': ['coalesce', ['get', 'color'], '#f85149'],
                    'circle-opacity': 0.22,
                    'circle-blur': 0.85
                }
            });
            _map.addLayer({
                id: 'evt-points', type: 'circle', source: 'events-cluster',
                filter: ['!', ['has', 'point_count']],
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['coalesce', ['get', 'riskScore'], 0.35], 0, 3.5, 1, 7.5],
                    'circle-color': ['coalesce', ['get', 'color'], '#f85149'],
                    'circle-stroke-width': 1,
                    'circle-stroke-color': 'rgba(8,12,16,0.95)',
                    'circle-opacity': 0.95
                }
            });
            _map.addSource('asset-trails', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'trails', type: 'line', source: 'asset-trails',
                layout: { 'line-join': 'round', 'line-cap': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-width': 1, 'line-opacity': 0.35, 'line-dasharray': [4, 6] }
            });
            _map.addSource('ghost-tracks', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'ghost-tracks-layer', type: 'line', source: 'ghost-tracks',
                layout: { 'line-join': 'round', 'line-cap': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-width': 2.5, 'line-opacity': 0.75, 'line-dasharray': [2, 4] }
            });
            await Promise.all([
                _loadImageFromSVG('icon-aircraft',         _aircraftSVG('#e3b341'), 32, 32),
                _loadImageFromSVG('icon-aircraft-hostile', _aircraftSVG('#f85149'), 32, 32),
                _loadImageFromSVG('icon-vessel',           _vesselSVG('#58a6ff'),   24, 24)
            ]);

            _map.addSource('assets-source', { type: 'geojson', data: emptyGJ() });
            _map.addLayer({
                id: 'assets', type: 'symbol', source: 'assets-source',
                layout: { 'icon-image': ['get', 'icon'], 'icon-rotate': ['get', 'heading'], 'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-size': 1.0 }
            });
            _map.on('click', 'assets', e => {
                if (!e.features?.length) return;
                new maplibregl.Popup({ closeButton: false, offset: 14, className: 'vi-popup' })
                    .setLngLat(e.lngLat).setHTML(_assetPopup(e.features[0].properties)).addTo(_map);
            });
            _map.on('mouseenter', 'assets',   () => { _map.getCanvas().style.cursor = 'crosshair'; });
            _map.on('mouseleave', 'assets',   () => { _map.getCanvas().style.cursor = ''; });
            _map.on('mouseenter', 'airspace-fill', e => {
                if (!e.features?.length) return;
                const p = e.features[0].properties;
                new maplibregl.Popup({ closeButton: false, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(`<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#c9d1d9;padding:4px;">
                        <div style="color:${p.closure_type==='NFZ'?'#f85149':'#d29922'};font-weight:700;margin-bottom:4px;">${p.closure_type || 'AIRSPACE'}</div>
                        <div>${p.name || 'Closure Zone'}</div>
                        ${p.reason ? `<div style="color:#6e7681;font-size:9px;margin-top:3px;">${p.reason}</div>` : ''}
                    </div>`)
                    .addTo(_map);
            });
            _map.on('click', 'evt-clusters', e => {
                const feats = _map.queryRenderedFeatures(e.point, { layers: ['evt-clusters'] });
                if (!feats.length) return;
                _map.getSource('events-cluster').getClusterExpansionZoom(feats[0].properties.cluster_id, (err, z) => {
                    if (!err) _map.easeTo({ center: feats[0].geometry.coordinates, zoom: z });
                });
            });
            _map.on('click', 'evt-points', e => {
                if (!e.features?.length) return;
                const feature = e.features[0];
                const props = feature.properties || {};
                new maplibregl.Popup({ offset: 10, closeButton: false, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(_eventPopup(props))
                    .addTo(_map);
                if (_dotNetRef && props.id) {
                    _dotNetRef.invokeMethodAsync('OnMarkerClick', String(props.id)).catch(() => {});
                }
            });
            _map.on('mouseenter', 'evt-points', () => { _map.getCanvas().style.cursor = 'pointer'; });
            _map.on('mouseleave', 'evt-points', () => { _map.getCanvas().style.cursor = ''; });

            _flush();

            // Viewport-driven assets: emit current bounds now, then on every pan/zoom.
            _map.on('moveend', _scheduleViewportEmit);
            _emitViewport();
        });

        _map.on('error', e => console.warn('[viMap] err:', e.error?.message));
        return true;
    }

    function _assetPopup(p) {
        const isAir = p.type === 'aircraft';
        return `<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#c9d1d9;min-width:180px;padding:2px 0;">
            <div style="color:${isAir ? '#e3b341' : '#58a6ff'};font-weight:700;font-size:12px;margin-bottom:6px;">
                ${isAir ? 'AIR' : 'SEA'} ${p.label || p.id}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:10px;color:#6e7681;">
                <span>TYPE</span><span style="color:#c9d1d9;">${(p.type||'').toUpperCase()}</span>
                <span>SPD</span><span style="color:#c9d1d9;">${Math.round(p.speed||0)} kt</span>
                <span>HDG</span><span style="color:#c9d1d9;">${Math.round(p.heading||0)} deg</span>
            </div>
        </div>`;
    }

    function _eventPopup(p) {
        return `<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#c9d1d9;max-width:260px;padding:2px 0;">
            <div style="color:${p.color || '#f85149'};font-size:9px;font-weight:700;letter-spacing:0.1em;margin-bottom:4px;">EVENT SIGNAL</div>
            <div style="line-height:1.4;margin-bottom:6px;">${p.title || 'Untitled event'}</div>
            <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 8px;font-size:9px;color:#6e7681;">
                <span>SRC</span><span style="color:#c9d1d9;">${p.source || 'UNK'}</span>
                <span>RISK</span><span style="color:#c9d1d9;">${p.risk || '--'}</span>
                <span>SENT</span><span style="color:#c9d1d9;">${p.sentiment || 'mixed'}</span>
            </div>
        </div>`;
    }

    function _normalizeAssetBatchPayload(payload) {
        if (Array.isArray(payload)) return payload;
        if (Array.isArray(payload?.assets)) return payload.assets;
        if (payload && typeof payload === 'object' && typeof payload.lat === 'number' && typeof payload.lon === 'number') {
            return [payload];
        }
        if (payload && typeof payload === 'object' && typeof payload.assetId === 'string') {
            return [payload];
        }
        return [];
    }

    function updateAssetsBatch(assets) {
        const assetList = _normalizeAssetBatchPayload(assets);
        if (!assetList.length) {
            _staticAssetFeatures = null;
            Object.keys(_targets).forEach(id => {
                delete _targets[id];
                delete _current[id];
                delete _history[id];
            });
            _whenReady(() => _pushCurrentToMap());
            return;
        }
        if (assetList.length > 1500) {
            Object.keys(_targets).forEach(id => {
                delete _targets[id];
                delete _current[id];
                delete _history[id];
            });
            _staticAssetFeatures = assetList
                .filter(a => a && typeof a === 'object' && typeof a.lat === 'number' && typeof a.lon === 'number')
                .map(a => {
                    const type = (a.assetType || '').toLowerCase() === 'aircraft' ? 'aircraft' : 'vessel';
                    const isHostile = type === 'aircraft' && (a.altitude || 0) > 35000;
                    const color = isHostile ? '#f85149' : (type === 'aircraft' ? '#e3b341' : '#58a6ff');
                    return {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [a.lon, a.lat] },
                        properties: {
                            id: a.assetId || a.label || `asset-${a.lat}-${a.lon}`,
                            icon: isHostile ? 'icon-aircraft-hostile' : (type === 'aircraft' ? 'icon-aircraft' : 'icon-vessel'),
                            heading: a.heading || 0,
                            label: a.label || a.assetId || 'asset',
                            type,
                            speed: a.speed || 0,
                            color
                        }
                    };
                });
            _whenReady(() => _pushCurrentToMap());
            return;
        }
        _staticAssetFeatures = null;
        const nextIds = new Set();
        assetList.forEach(a => {
            if (!a || typeof a !== 'object') return;
            if (typeof a.lat !== 'number' || typeof a.lon !== 'number') return;
            const isAir     = (a.assetType || '').toLowerCase() === 'aircraft';
            const isHostile = isAir && (a.altitude || 0) > 35000;
            const id        = a.assetId || a.label || `asset-${a.lat}-${a.lon}`;
            nextIds.add(id);
            const color     = isHostile ? '#f85149' : (isAir ? '#e3b341' : '#58a6ff');

            if (!_history[id]) _history[id] = [];
            const last = _history[id].at(-1);
            if (!last || last[0] !== a.lon || last[1] !== a.lat) {
                _history[id].push([a.lon, a.lat]);
                if (_history[id].length > 12) _history[id].shift();
            }
            if (!_current[id]) _current[id] = { lat: a.lat, lon: a.lon, heading: a.heading || 0 };
            _targets[id] = {
                lat: a.lat, lon: a.lon, heading: a.heading || 0,
                type: isAir ? 'aircraft' : 'vessel',
                icon: isHostile ? 'icon-aircraft-hostile' : (isAir ? 'icon-aircraft' : 'icon-vessel'),
                label: a.label || id, speed: a.speed || 0, color
            };
        });
        Object.keys(_targets).forEach(id => {
            if (!nextIds.has(id)) {
                delete _targets[id];
                delete _current[id];
                delete _history[id];
            }
        });
        _whenReady(() => _pushCurrentToMap());
    }

    function updateHeatmap(points) {
        _whenReady(() => {
            if (!_map.getSource('events-heat')) return;
            const heatPoints = Array.isArray(points) ? points : [];
            const features = _showHeat ? heatPoints.map(p => ({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [p[1], p[0]] },
                properties: { weight: typeof p[2] === 'number' ? p[2] : 1 }
            })) : [];
            _map.getSource('events-heat').setData({ type: 'FeatureCollection', features });
        });
    }

    function renderEvents(events) {
        _whenReady(() => {
            if (!_map.getSource('events-cluster')) return;
            const eventList = Array.isArray(events) ? events : [];
            const features = _showEvents ? eventList.filter(e => typeof e.lat === 'number' && typeof e.lon === 'number').map(e => ({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
                properties: {
                    id: e.id,
                    title: e.title,
                    source: e.source,
                    risk: typeof e.risk === 'number' ? e.risk.toFixed(2) : '--',
                    riskScore: typeof e.risk === 'number' ? e.risk : 0.35,
                    sentiment: e.sentimentLabel || 'mixed',
                    color: e.color || '#f85149'
                }
            })) : [];
            _map.getSource('events-cluster').setData({ type: 'FeatureCollection', features });
        });
    }

    // jammingData: { tiles: [{lat_min,lon_min,lat_max,lon_max,count,density}] }
    function updateJamming(jammingData) {
        _whenReady(() => {
            if (!_map.getSource('jamming-source')) return;
            const tiles = (jammingData && jammingData.tiles) ? jammingData.tiles : (Array.isArray(jammingData) ? jammingData : []);
            const features = _showJamming ? tiles.map(t => {
                const lat = t.lat ?? t.Lat ?? t.latMin ?? t.lat_min ?? 0;
                const lon = t.lon ?? t.Lon ?? t.lonMin ?? t.lon_min ?? 0;
                const latMin = t.lat_min ?? t.latMin ?? lat;
                const lonMin = t.lon_min ?? t.lonMin ?? lon;
                const latMax = t.lat_max ?? t.latMax ?? (lat + 1);
                const lonMax = t.lon_max ?? t.lonMax ?? (lon + 1);
                return _bboxPolygon(
                    latMin, lonMin, latMax, lonMax,
                    { intensity: t.density ?? t.Density ?? t.intensity ?? t.Intensity ?? Math.min((t.count || t.Count || 1) / 10, 1), count: t.count || t.Count || 0 }
                );
            }).filter(Boolean) : [];
            _map.getSource('jamming-source').setData({ type: 'FeatureCollection', features });
        });
    }

    // airspaceData: { closures: [{name,type,lat_min,lon_min,lat_max,lon_max,reason,active}] }
    function updateAirspace(airspaceData) {
        _whenReady(() => {
            if (!_map.getSource('airspace-source')) return;
            const closures = (airspaceData && airspaceData.closures)
                ? airspaceData.closures
                : (Array.isArray(airspaceData) ? airspaceData : []);
            const features = _showAirspace ? closures
                .filter(c => c.active !== false)
                .map(c => {
                    // If polygon provided, use it; otherwise build from bbox
                    if (c.polygon || c.Polygon) {
                        return {
                            type: 'Feature',
                            geometry: { type: 'Polygon', coordinates: [c.polygon || c.Polygon] },
                            properties: { name: c.name || c.Name || c.title || c.Title, closure_type: (c.type || c.Type || 'TFR').toUpperCase(), reason: c.reason || c.Reason || c.description || c.Description }
                        };
                    }
                    return _bboxPolygon(
                        c.lat_min ?? c.latMin ?? c.bbox_lat_min ?? c.bboxLatMin,
                        c.lon_min ?? c.lonMin ?? c.bbox_lon_min ?? c.bboxLonMin,
                        c.lat_max ?? c.latMax ?? c.bbox_lat_max ?? c.bboxLatMax,
                        c.lon_max ?? c.lonMax ?? c.bbox_lon_max ?? c.bboxLonMax,
                        { name: c.name || c.Name || c.title || c.Title || c.identifier || c.Identifier, closure_type: (c.type || c.Type || 'TFR').toUpperCase(), reason: c.reason || c.Reason || c.description || c.Description }
                    );
                }).filter(Boolean) : [];
            _map.getSource('airspace-source').setData({ type: 'FeatureCollection', features });
        });
    }

    // passData: { passes: [{sat_id,sat_name,points:[{lat,lon,time}]}] }
    function updateSatellitePasses(passData) {
        _whenReady(() => {
            if (!_map.getSource('satellite-source')) return;
            const passes = (passData && passData.passes)
                ? passData.passes
                : (Array.isArray(passData) ? passData : []);
            const features = [];
            if (_showSatellite) {
                passes.forEach(p => {
                    let pts = (p.points || p.Points || []).filter(pt => typeof (pt.lat ?? pt.Lat) === 'number' && typeof (pt.lon ?? pt.Lon) === 'number')
                        .map(pt => ({ lat: pt.lat ?? pt.Lat, lon: pt.lon ?? pt.Lon, time: pt.time ?? pt.Time }));
                    const baseLat = p.lat ?? p.Lat;
                    const baseLon = p.lon ?? p.Lon;
                    if (pts.length < 2 && typeof baseLat === 'number' && typeof baseLon === 'number') {
                        pts = [
                            { lat: Math.max(-85, baseLat - 2.5), lon: baseLon - 4, time: p.aos ?? p.Aos },
                            { lat: baseLat, lon: baseLon, time: p.aos ?? p.Aos },
                            { lat: Math.min(85, baseLat + 2.5), lon: baseLon + 4, time: p.los ?? p.Los }
                        ];
                    }
                    if (pts.length < 2) return;
                    // Mark AOS (first visible) and LOS (last visible) as points
                    features.push({
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [pts[0].lon, pts[0].lat] },
                        properties: { sat: p.sat_name || p.satName || p.SatName || p.sat_id || p.satId || p.SatId, phase: 'AOS' }
                    });
                    features.push({
                        type: 'Feature',
                        geometry: { type: 'LineString', coordinates: pts.map(pt => [pt.lon, pt.lat]) },
                        properties: { sat: p.sat_name || p.satName || p.SatName || p.sat_id || p.satId || p.SatId }
                    });
                });
            }
            _map.getSource('satellite-source').setData({ type: 'FeatureCollection', features });
        });
    }

    // narrativeData: { countries: [{lat,lon,country,sentiment,count}] } or array of heat points
    function updateNarrativeHeat(narrativeData) {
        _whenReady(() => {
            if (!_map.getSource('narrative-heat')) return;
            let points = [];
            if (narrativeData && narrativeData.countries) points = narrativeData.countries;
            else if (Array.isArray(narrativeData)) points = narrativeData;
            const features = _showNarrative ? points
                .filter(p => p && typeof p.lat === 'number' && typeof p.lon === 'number')
                .map(p => ({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
                properties: {
                    weight: Math.min(Math.max((() => {
                        const risk = typeof p.riskScore === 'number' ? p.riskScore : (typeof p.risk_score === 'number' ? p.risk_score : Math.abs(((p.sentiment || 0.5) - 0.5) * 2));
                        const negRatio = typeof p.negative_ratio === 'number'
                            ? p.negative_ratio
                            : (typeof p.negativeCount === 'number' || typeof p.negative_count === 'number')
                                ? ((p.negativeCount ?? p.negative_count ?? 0) / Math.max((p.count || 1), 1))
                                : 0;
                        const socialRatio = typeof p.social_ratio === 'number'
                            ? p.social_ratio
                            : (typeof p.socialCount === 'number' || typeof p.social_count === 'number')
                                ? ((p.socialCount ?? p.social_count ?? 0) / Math.max((p.count || 1), 1))
                                : 0;
                        const negSocialRatio = typeof p.negative_social_ratio === 'number'
                            ? p.negative_social_ratio
                            : ((p.negativeSocialCount ?? p.negative_social_count ?? 0) / Math.max((p.socialCount ?? p.social_count ?? 0), 1));
                        const volume = Math.max(Math.log((p.count || 1) + 1) / 5, 0.25);
                        return ((risk * 0.55) + (negRatio * 0.25) + (negSocialRatio * 0.15) + (socialRatio * 0.05)) * volume;
                    })(), 0.08), 1)
                }
            })) : [];
            _map.getSource('narrative-heat').setData({ type: 'FeatureCollection', features });
        });
    }

    // escData: array of { region|country, lat, lon, score, trend } OR { hotspots:[...] }
    function updateEscalation(escData) {
        _whenReady(() => {
            if (!_map.getSource('escalation-source')) return;
            let points = [];
            if (escData && Array.isArray(escData.hotspots)) points = escData.hotspots;
            else if (escData && Array.isArray(escData.scores)) points = escData.scores;
            else if (Array.isArray(escData)) points = escData;
            const features = points
                .filter(p => p && typeof p.lat === 'number' && typeof p.lon === 'number')
                .map(p => ({
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
                    properties: {
                        score: Math.max(0, Math.min(1, p.score ?? p.probability ?? 0)),
                        region: p.region || p.country || '',
                        trend: p.trend || ''
                    }
                }));
            _map.getSource('escalation-source').setData({ type: 'FeatureCollection', features });
        });
    }

    function _setVis(layerIds, visible) {
        layerIds.forEach(id => {
            if (_map.getLayer(id)) _map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
        });
    }

    function toggleLayer(name) {
        _whenReady(() => {
            switch (name) {
                case 'flights':
                    _showFlights = !_showFlights;
                    if (_map.getLayer('assets')) {
                        const f = ['any'];
                        if (_showFlights) f.push(['==', ['get', 'type'], 'aircraft']);
                        if (_showVessels) f.push(['==', ['get', 'type'], 'vessel']);
                        _map.setFilter('assets', _showFlights || _showVessels ? f : ['==', ['get', 'type'], '__none__']);
                    }
                    break;
                case 'vessels':
                    _showVessels = !_showVessels;
                    if (_map.getLayer('assets')) {
                        const f = ['any'];
                        if (_showFlights) f.push(['==', ['get', 'type'], 'aircraft']);
                        if (_showVessels) f.push(['==', ['get', 'type'], 'vessel']);
                        _map.setFilter('assets', _showFlights || _showVessels ? f : ['==', ['get', 'type'], '__none__']);
                    }
                    break;
                case 'events':
                    _showEvents = !_showEvents;
                    _setVis(['evt-clusters','evt-cluster-count','evt-points','evt-points-glow'], _showEvents);
                    break;
                case 'heat':
                    _showHeat = !_showHeat;
                    _setVis(['heatmap'], _showHeat);
                    break;
                case 'jamming':
                    _showJamming = !_showJamming;
                    _setVis(['jamming-fill','jamming-outline'], _showJamming);
                    break;
                case 'airspace':
                    _showAirspace = !_showAirspace;
                    _setVis(['airspace-fill','airspace-outline'], _showAirspace);
                    break;
                case 'satellite':
                    _showSatellite = !_showSatellite;
                    _setVis(['satellite-track','satellite-pts'], _showSatellite);
                    break;
                case 'narrative':
                    _showNarrative = !_showNarrative;
                    _setVis(['narrative-heatmap'], _showNarrative);
                    break;
                case 'escalation':
                    _showEscalation = !_showEscalation;
                    _setVis(['escalation-fill'], _showEscalation);
                    break;
            }
        });

        return {
            flights: _showFlights, vessels: _showVessels, events: _showEvents, heat: _showHeat,
            jamming: _showJamming, airspace: _showAirspace, satellite: _showSatellite,
            narrative: _showNarrative, escalation: _showEscalation
        };
    }

    function flyTo(lat, lon, z) {
        _whenReady(() => { _map.flyTo({ center: [lon, lat], zoom: z || 8, speed: 1.2, essential: true }); });
    }

    function focusAsset(asset) {
        const payload = _normalizeAssetBatchPayload(asset)[0];
        if (!payload || typeof payload.lat !== 'number' || typeof payload.lon !== 'number') return false;
        updateAssetsBatch([payload]);
        _whenReady(() => {
            _map.flyTo({ center: [payload.lon, payload.lat], zoom: payload.zoom || 10, speed: 1.35, essential: true });
            if (_focusPopup) {
                try { _focusPopup.remove(); } catch (_) {}
                _focusPopup = null;
            }
            _focusPopup = new maplibregl.Popup({ offset: 16, closeButton: true, closeOnClick: false, className: 'vi-popup' })
                .setLngLat([payload.lon, payload.lat])
                .setHTML(_assetPopup({
                    id: payload.assetId,
                    label: payload.label,
                    type: (payload.assetType || 'asset').toLowerCase(),
                    speed: payload.speed || 0,
                    heading: payload.heading || 0
                }))
                .addTo(_map);
        });
        return true;
    }

    function renderGhostTracks(fc) {
        _whenReady(() => {
            const src = _map.getSource('ghost-tracks');
            if (!src) return;
            src.setData(fc);
            if (fc.features?.length) {
                const b = new maplibregl.LngLatBounds();
                fc.features.forEach(f => (f.geometry.coordinates||[]).forEach(c => b.extend(c)));
                _map.fitBounds(b, { padding: 60, duration: 1200 });
            }
        });
    }

    function openGraph(id) {
        if (_dotNetRef && id) _dotNetRef.invokeMethodAsync('OpenGraphPanel', id).catch(() => {});
    }
    // Called by Timeline.razor to paint a static historical snapshot

    function setReplaySnapshot(assets, events) {
        // Clear live animation targets and set static positions
        Object.keys(_targets).forEach(k => delete _targets[k]);
        Object.keys(_current).forEach(k => delete _current[k]);
        Object.keys(_history).forEach(k => delete _history[k]);

        if (_normalizeAssetBatchPayload(assets).length) updateAssetsBatch(assets);
        if (events && events.length) renderEvents(events);
    }

    // ── Workspace-scoped map (separate MapLibre instance per workspace detail page) ──────────

    function initWorkspaceMap(containerId, minLat, maxLat, minLon, maxLon, assets, eventData, dotNetRef) {
        if (_wsMap) { try { _wsMap.remove(); } catch(_) {} _wsMap = null; }
        _wsMapContainerId = containerId;
        _wsDotNetRef = dotNetRef || null;

        const container = document.getElementById(containerId);
        if (!container) { console.warn('[wsMap] container not found:', containerId); return false; }

        const cLon = (minLon != null && maxLon != null) ? (minLon + maxLon) / 2 : defaultCenter[0];
        const cLat = (minLat != null && maxLat != null) ? (minLat + maxLat) / 2 : defaultCenter[1];

        _wsMap = new maplibregl.Map({
            container, style: defaultStyleUrl,
            center: [cLon, cLat], zoom: 5,
            attributionControl: false,
        });
        _wsMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
        _wsMap.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 80 }), 'bottom-left');

        _wsMap.on('load', async () => {
            await Promise.all([
                _loadImageFromSVGForMap(_wsMap, 'ws-icon-aircraft', _aircraftSVG('#e3b341'), 32, 32),
                _loadImageFromSVGForMap(_wsMap, 'ws-icon-aircraft-alert', _aircraftSVG('#f87171'), 32, 32),
                _loadImageFromSVGForMap(_wsMap, 'ws-icon-vessel', _vesselSVG('#60a5fa'), 28, 28),
                _loadImageFromSVGForMap(_wsMap, 'ws-icon-vessel-alert', _vesselSVG('#f87171'), 28, 28)
            ]);

            // AOI bounding box
            if (minLat != null && maxLat != null && minLon != null && maxLon != null) {
                _wsMap.addSource('ws-aoi', { type: 'geojson', data: _bboxPolygon(minLat, minLon, maxLat, maxLon, {}) });
                _wsMap.addLayer({ id: 'ws-aoi-fill', type: 'fill', source: 'ws-aoi',
                    paint: { 'fill-color': '#a78bfa', 'fill-opacity': 0.06 } });
                _wsMap.addLayer({ id: 'ws-aoi-line', type: 'line', source: 'ws-aoi',
                    paint: { 'line-color': '#a78bfa', 'line-width': 1.5, 'line-dasharray': [4, 2], 'line-opacity': 0.7 } });
                _wsMap.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 50, duration: 0 });
            }

            // Event markers (render first so assets layer appears on top)
            const evFeatures = (eventData || []).map(e => {
                const risk = typeof e.riskScore === 'number' ? e.riskScore : 0;
                return {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
                    properties: {
                        eventId:   e.eventId || '',
                        title:     e.title   || '',
                        eventType: e.eventType || '',
                        riskScore: risk,
                        color: risk >= 0.7 ? '#f85149' : (risk >= 0.4 ? '#d29922' : '#3fb950')
                    }
                };
            });
            _wsMap.addSource('ws-events', { type: 'geojson', data: { type: 'FeatureCollection', features: evFeatures } });
            _wsMap.addLayer({ id: 'ws-events-glow', type: 'circle', source: 'ws-events',
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['get', 'riskScore'], 0, 7, 1, 19],
                    'circle-color':  ['get', 'color'],
                    'circle-opacity': 0.14,
                    'circle-blur': 0.8
                }
            });
            _wsMap.addLayer({ id: 'ws-events-dots', type: 'circle', source: 'ws-events',
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['get', 'riskScore'], 0, 3.5, 1, 7],
                    'circle-color':  ['get', 'color'],
                    'circle-stroke-width': 1,
                    'circle-stroke-color': 'rgba(9,9,11,0.9)',
                    'circle-opacity': 0.9
                }
            });

            // Asset markers
            const features = (assets || []).map(a => ({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [a.lon, a.lat] },
                properties: {
                    assetId:  a.assetId || a.name || '',
                    name:     a.name || '',
                    assetType: a.type || 'unknown',
                    anomaly:  a.anomaly ? 1 : 0,
                    withinAoi: a.withinAoi === false ? 0 : 1,
                    color:    a.anomaly ? '#f87171' : (a.withinAoi === false ? '#8b949e' : (a.type === 'vessel' ? '#60a5fa' : '#fbbf24')),
                    speed:    a.speed || 0,
                    icon:     a.anomaly
                        ? (a.type === 'vessel' ? 'ws-icon-vessel-alert' : 'ws-icon-aircraft-alert')
                        : (a.type === 'vessel' ? 'ws-icon-vessel' : 'ws-icon-aircraft'),
                    heading:  a.heading || 0
                }
            }));
            _wsMap.addSource('ws-assets', { type: 'geojson', data: { type: 'FeatureCollection', features } });

            // Anomaly glow
            _wsMap.addLayer({ id: 'ws-anomaly-glow', type: 'circle', source: 'ws-assets',
                filter: ['==', ['get', 'anomaly'], 1],
                paint: { 'circle-radius': 18, 'circle-color': '#f87171', 'circle-opacity': 0.15, 'circle-blur': 0.9 }
            });
            _wsMap.addLayer({ id: 'ws-assets-dots', type: 'symbol', source: 'ws-assets',
                layout: {
                    'icon-image': ['get', 'icon'],
                    'icon-size': ['case', ['==', ['get', 'anomaly'], 1], 0.9, 0.72],
                    'icon-rotate': ['get', 'heading'],
                    'icon-rotation-alignment': 'map',
                    'icon-allow-overlap': true,
                    'icon-ignore-placement': true
                },
                paint: { 'icon-opacity': ['case', ['==', ['get', 'withinAoi'], 1], 0.95, 0.45] }
            });

            _wsMap.on('click', 'ws-events-dots', e => {
                if (!e.features?.length) return;
                const p = e.features[0].properties;
                new maplibregl.Popup({ closeButton: false, offset: 12, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(`<strong>${p.title || 'Event signal'}</strong><span>EVENT${p.eventType ? ' · ' + String(p.eventType).toUpperCase() : ''} · RISK ${Number(p.riskScore).toFixed(2)}</span>`)
                    .addTo(_wsMap);
                if (_wsDotNetRef && p.eventId) {
                    _wsDotNetRef.invokeMethodAsync('OnWsEventSelected', String(p.eventId)).catch(() => {});
                }
            });
            _wsMap.on('mouseenter', 'ws-events-dots', () => { _wsMap.getCanvas().style.cursor = 'pointer'; });
            _wsMap.on('mouseleave', 'ws-events-dots', () => { _wsMap.getCanvas().style.cursor = ''; });

            _wsMap.on('click', 'ws-assets-dots', e => {
                if (!e.features?.length) return;
                const p = e.features[0].properties;
                const isAnomaly = Number(p.anomaly || 0) === 1;
                const speed = Number(p.speed || 0);
                new maplibregl.Popup({ closeButton: false, offset: 12, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(`<strong>${p.name || p.assetId || 'Moving asset'}</strong><span>${String(p.assetType || 'ASSET').toUpperCase()}${isAnomaly ? ' · ANOMALY' : ''}${speed > 0 ? ' · ' + speed.toFixed(1) + ' KT' : ''}</span>`)
                    .addTo(_wsMap);
                if (_wsDotNetRef && p.assetId) {
                    _wsDotNetRef.invokeMethodAsync('OnWsAssetSelected', String(p.assetId)).catch(() => {});
                }
            });
            _wsMap.on('mouseenter', 'ws-assets-dots', () => { _wsMap.getCanvas().style.cursor = 'pointer'; });
            _wsMap.on('mouseleave', 'ws-assets-dots', () => { _wsMap.getCanvas().style.cursor = ''; });
        });

        _wsMap.on('error', e => console.warn('[wsMap]', e.error?.message));
        return true;
    }

    function _workspaceEventFeatures(eventData) {
        return (eventData || []).map(e => {
            const risk = typeof e.riskScore === 'number' ? e.riskScore : 0;
            return {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
                properties: {
                    eventId:   e.eventId || '',
                    title:     e.title   || '',
                    eventType: e.eventType || '',
                    riskScore: risk,
                    color: risk >= 0.7 ? '#f85149' : (risk >= 0.4 ? '#d29922' : '#3fb950')
                }
            };
        });
    }

    function _workspaceAssetFeatures(assets) {
        return (assets || []).map(a => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [a.lon, a.lat] },
            properties: {
                assetId:  a.assetId || a.name || '',
                name:     a.name || '',
                assetType: a.type || 'unknown',
                anomaly:  a.anomaly ? 1 : 0,
                withinAoi: a.withinAoi === false ? 0 : 1,
                color:    a.anomaly ? '#f87171' : (a.withinAoi === false ? '#8b949e' : (a.type === 'vessel' ? '#60a5fa' : '#fbbf24')),
                speed:    a.speed || 0,
                icon:     a.anomaly
                    ? (a.type === 'vessel' ? 'ws-icon-vessel-alert' : 'ws-icon-aircraft-alert')
                    : (a.type === 'vessel' ? 'ws-icon-vessel' : 'ws-icon-aircraft'),
                heading:  a.heading || 0
            }
        }));
    }

    function updateWorkspaceMap(containerId, minLat, maxLat, minLon, maxLon, assets, eventData) {
        if (!_wsMap || _wsMapContainerId !== containerId || !_wsMap.loaded()) return false;
        const evSource = _wsMap.getSource('ws-events');
        const assetSource = _wsMap.getSource('ws-assets');
        if (evSource) evSource.setData({ type: 'FeatureCollection', features: _workspaceEventFeatures(eventData) });
        if (assetSource) assetSource.setData({ type: 'FeatureCollection', features: _workspaceAssetFeatures(assets) });
        if (minLat != null && maxLat != null && minLon != null && maxLon != null && !_wsMap.__viOverviewFitted) {
            _wsMap.__viOverviewFitted = true;
            try { _wsMap.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 50, duration: 0 }); } catch (_) {}
        }
        return true;
    }

    function setLayerVisible(containerId, layerName, visible) {
        if (!_wsMap) return;
        const layerMap = {
            'assets': ['ws-assets-dots', 'ws-anomaly-glow'],
            'events': ['ws-events-dots', 'ws-events-glow'],
        };
        const layers = layerMap[layerName];
        if (!layers) return;
        layers.forEach(id => {
            if (_wsMap.getLayer(id))
                _wsMap.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
        });
    }

    function exportImage() {
        if (!_map) return;
        // MapLibre renders to a WebGL canvas -- preserve drawing buffer must be on.
        // We attempt getCanvas(); if toDataURL fails (preserveDrawingBuffer:false) we warn.
        try {
            const canvas = _map.getCanvas();
            const url = canvas.toDataURL('image/png');
            const a = document.createElement('a');
            a.href = url;
            a.download = `vision-i-cop-${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (e) {
            console.warn('[viMap] exportImage: preserveDrawingBuffer may be false', e);
        }
    }

    function exportImageWithOverlay(summaryText, riskLevel) {
        if (!_map) return;
        try {
            const src = _map.getCanvas();
            const out = document.createElement('canvas');
            out.width  = src.width;
            out.height = src.height;
            const ctx  = out.getContext('2d');

            // Draw map
            ctx.drawImage(src, 0, 0);

            // JARVIS strip overlay at top
            const barH = Math.max(28, Math.round(src.height * 0.04));
            ctx.fillStyle = 'rgba(8,12,16,0.92)';
            ctx.fillRect(0, 0, out.width, barH);

            const fontSize = Math.round(barH * 0.38);
            ctx.font      = `bold ${fontSize}px 'JetBrains Mono',monospace`;
            ctx.fillStyle = '#3fb950';
            ctx.fillText('▲ JARVIS', 12, Math.round(barH * 0.68));

            const lblW = ctx.measureText('▲ JARVIS').width + 20;
            ctx.font      = `${Math.round(barH * 0.34)}px monospace`;
            ctx.fillStyle = '#8b949e';
            const maxSumW = out.width - lblW - 120;
            let sumText   = (summaryText || '').slice(0, 150);
            // Truncate to fit
            while (sumText.length > 10 && ctx.measureText(sumText + '…').width > maxSumW) {
                sumText = sumText.slice(0, -1);
            }
            ctx.fillText(sumText + (sumText.length < (summaryText || '').length ? '…' : ''), lblW, Math.round(barH * 0.68));

            // Risk badge
            const riskColors = { CRITICAL: '#f85149', HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#3fb950' };
            ctx.font      = `bold ${Math.round(barH * 0.36)}px monospace`;
            ctx.fillStyle = riskColors[(riskLevel || 'LOW').toUpperCase()] || '#3fb950';
            ctx.fillText((riskLevel || 'LOW').toUpperCase(), out.width - 90, Math.round(barH * 0.68));

            // Timestamp at bottom-left
            ctx.font      = `${Math.round(barH * 0.3)}px monospace`;
            ctx.fillStyle = '#3d444d';
            ctx.fillText(new Date().toISOString().slice(0, 19) + 'Z', 12, out.height - 8);

            // Download
            const a = document.createElement('a');
            a.href     = out.toDataURL('image/png');
            a.download = `vision-i-cop-${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (e) {
            console.warn('[viMap] exportImageWithOverlay failed:', e);
            // Fall back to plain export
            exportImage();
        }
    }

    return {
        initMap, resize, updateAssetsBatch, updateHeatmap, renderEvents,
        updateJamming, updateAirspace, updateSatellitePasses, updateNarrativeHeat, updateEscalation,
        toggleLayer, flyTo, focusAsset, renderGhostTracks, openGraph, setReplaySnapshot,
        exportImage, exportImageWithOverlay,
        initWorkspaceMap, updateWorkspaceMap, setLayerVisible
    };
})();

window.viAssetMiniMap = (function () {
    'use strict';

    const mapCfg = window.VisionIMapConfig || {};
    const defaultStyleUrl = typeof mapCfg.styleUrl === 'string' && mapCfg.styleUrl.length
        ? mapCfg.styleUrl
        : 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
    const maps = {};

    function finiteNumber(v) {
        if (typeof v === 'number') return Number.isFinite(v) ? v : null;
        if (typeof v === 'string' && v.trim() !== '') {
            const n = Number(v);
            return Number.isFinite(n) ? n : null;
        }
        return null;
    }

    function iconFor(type) {
        const t = String(type || '').toLowerCase();
        if (t === 'aircraft') return 'flight';
        if (t === 'vessel') return 'directions_boat';
        return 'location_on';
    }

    function colorFor(type) {
        const t = String(type || '').toLowerCase();
        if (t === 'aircraft') return '#fbbf24';
        if (t === 'vessel') return '#34d399';
        return '#a78bfa';
    }

    function markerElement(asset) {
        const type = asset.assetType || asset.type || 'asset';
        const color = colorFor(type);
        const el = document.createElement('div');
        el.style.width = '34px';
        el.style.height = '34px';
        el.style.borderRadius = '999px';
        el.style.display = 'grid';
        el.style.placeItems = 'center';
        el.style.color = color;
        el.style.background = 'rgba(3,7,18,.82)';
        el.style.border = `1px solid ${color}`;
        el.style.boxShadow = `0 0 0 5px ${color}24, 0 0 26px ${color}55`;
        el.innerHTML = `<span class="material-symbols-outlined" style="font-size:21px;line-height:1;">${iconFor(type)}</span>`;
        return el;
    }

    function popupHtml(asset) {
        const label = asset.label || asset.name || asset.assetId || 'Asset';
        const type = String(asset.assetType || asset.type || 'asset').toUpperCase();
        const speed = Math.round(finiteNumber(asset.speed) || 0);
        const heading = Math.round(finiteNumber(asset.heading) || 0);
        const altitude = Math.round(finiteNumber(asset.altitude) || 0);
        const altitudeRow = altitude > 0 ? `<span>ALT</span><b>${altitude} m</b>` : '';
        return `<div class="vi-asset-popup">
            <strong>${label}</strong>
            <div>
                <span>TYPE</span><b>${type}</b>
                <span>SPD</span><b>${speed} kt</b>
                <span>HDG</span><b>${heading} deg</b>
                ${altitudeRow}
            </div>
        </div>`;
    }

    function draw(entry, asset) {
        const lat = finiteNumber(asset.lat);
        const lon = finiteNumber(asset.lon);
        if (lat === null || lon === null || !entry.loaded) return;
        const lngLat = [lon, lat];
        entry.map.jumpTo({ center: lngLat, zoom: 9 });
        if (entry.marker) entry.marker.remove();
        if (entry.popup) entry.popup.remove();
        entry.marker = new maplibregl.Marker({ element: markerElement(asset), anchor: 'center' })
            .setLngLat(lngLat)
            .addTo(entry.map);
        entry.popup = new maplibregl.Popup({ offset: 16, closeButton: false, closeOnClick: false, className: 'vi-popup' })
            .setLngLat(lngLat)
            .setHTML(popupHtml(asset))
            .addTo(entry.map);
    }

    function render(containerId, asset) {
        const container = document.getElementById(containerId);
        if (!container || !asset) return false;
        const lat = finiteNumber(asset.lat);
        const lon = finiteNumber(asset.lon);
        if (lat === null || lon === null) return false;

        let entry = maps[containerId];
        if (!entry) {
            const map = new maplibregl.Map({
                container,
                style: defaultStyleUrl,
                center: [lon, lat],
                zoom: 9,
                attributionControl: false,
                renderWorldCopies: false,
                interactive: true
            });
            map.addControl(new maplibregl.NavigationControl({ showCompass: false, showZoom: true }), 'bottom-right');
            map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 82 }), 'bottom-left');
            try { map.dragRotate.disable(); map.touchZoomRotate.disableRotation(); } catch (_) {}
            entry = maps[containerId] = { map, marker: null, popup: null, loaded: false };
            map.on('load', () => {
                entry.loaded = true;
                draw(entry, asset);
                setTimeout(() => { try { map.resize(); } catch (_) {} }, 80);
            });
        } else {
            draw(entry, asset);
            setTimeout(() => { try { entry.map.resize(); } catch (_) {} }, 40);
        }
        return true;
    }

    function dispose(containerId) {
        const entry = maps[containerId];
        if (!entry) return;
        try { entry.map.remove(); } catch (_) {}
        delete maps[containerId];
    }

    return { render, dispose };
})();

window.viDomainGlobe = (function () {
    'use strict';

    const mapCfg = window.VisionIMapConfig || {};
    const defaultStyleUrl = typeof mapCfg.styleUrl === 'string' && mapCfg.styleUrl.length
        ? mapCfg.styleUrl
        : 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
    const maps = {};
    const pending = {};
    const observers = {};
    const retries = {};

    function emptyGJ() { return { type: 'FeatureCollection', features: [] }; }
    function finite(v) { return typeof v === 'number' && Number.isFinite(v); }
    function number(v) {
        if (typeof v === 'number') return Number.isFinite(v) ? v : null;
        if (typeof v === 'string' && v.trim().length) {
            const parsed = Number(v);
            return Number.isFinite(parsed) ? parsed : null;
        }
        return null;
    }
    function val(o, ...keys) {
        for (const key of keys) {
            if (o && o[key] !== undefined && o[key] !== null) return o[key];
        }
        return null;
    }
    function bboxFeature(item, props, fallbackSize) {
        let latMin = number(val(item, 'lat_min', 'latMin', 'LatMin'));
        let lonMin = number(val(item, 'lon_min', 'lonMin', 'LonMin'));
        let latMax = number(val(item, 'lat_max', 'latMax', 'LatMax'));
        let lonMax = number(val(item, 'lon_max', 'lonMax', 'LonMax'));
        const lat = number(val(item, 'lat', 'Lat'));
        const lon = number(val(item, 'lon', 'Lon', 'lng', 'Lng'));

        if (![latMin, lonMin, latMax, lonMax].every(finite) && finite(lat) && finite(lon)) {
            const span = fallbackSize || 0.8;
            latMin = lat - span;
            latMax = lat + span;
            lonMin = lon - span;
            lonMax = lon + span;
        }
        if (![latMin, lonMin, latMax, lonMax].every(finite)) return null;
        return {
            type: 'Feature',
            geometry: {
                type: 'Polygon',
                coordinates: [[[lonMin, latMin], [lonMax, latMin], [lonMax, latMax], [lonMin, latMax], [lonMin, latMin]]]
            },
            properties: props || {}
        };
    }
    function pointFeature(item, props) {
        const lat = number(val(item, 'lat', 'Lat'));
        const lon = number(val(item, 'lon', 'Lon', 'lng', 'Lng'));
        if (!finite(lat) || !finite(lon)) return null;
        return { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] }, properties: props || {} };
    }
    function lineFeature(points, props) {
        const coords = (points || [])
            .map(p => [number(val(p, 'lon', 'Lon', 'lng', 'Lng')), number(val(p, 'lat', 'Lat'))])
            .filter(p => finite(p[0]) && finite(p[1]));
        if (coords.length < 2) return null;
        return { type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: props || {} };
    }
    function hexFeature(lat, lon, radius, props) {
        if (!finite(lat) || !finite(lon)) return null;
        const cosLat = Math.max(0.25, Math.cos(lat * Math.PI / 180));
        const coords = [];
        for (let i = 0; i < 6; i++) {
            const angle = (30 + i * 60) * Math.PI / 180;
            coords.push([
                lon + (Math.cos(angle) * radius) / cosLat,
                lat + Math.sin(angle) * radius
            ]);
        }
        coords.push(coords[0]);
        return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] }, properties: props || {} };
    }
    function centerOf(item) {
        const lat = number(val(item, 'lat', 'Lat'));
        const lon = number(val(item, 'lon', 'Lon', 'lng', 'Lng'));
        if (finite(lat) && finite(lon)) return [lat, lon];
        const latMin = number(val(item, 'lat_min', 'latMin', 'LatMin'));
        const lonMin = number(val(item, 'lon_min', 'lonMin', 'LonMin'));
        const latMax = number(val(item, 'lat_max', 'latMax', 'LatMax'));
        const lonMax = number(val(item, 'lon_max', 'lonMax', 'LonMax'));
        if ([latMin, lonMin, latMax, lonMax].every(finite)) return [(latMin + latMax) / 2, (lonMin + lonMax) / 2];
        return null;
    }

    function addSource(map, id) {
        if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: emptyGJ() });
    }

    function svgDataUri(svg) {
        return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
    }

    function loadDomainImage(map, id, svg, size) {
        return new Promise(resolve => {
            if (map.hasImage(id)) { resolve(); return; }
            const img = new Image(size, size);
            img.onload = () => {
                try { if (!map.hasImage(id)) map.addImage(id, img, { sdf: false }); } catch (_) {}
                resolve();
            };
            img.onerror = resolve;
            img.src = svgDataUri(svg);
        });
    }

    function domainAircraftSvg(color) {
        return `<svg viewBox="0 0 32 32" width="32" height="32" xmlns="http://www.w3.org/2000/svg">
            <path d="M16 2 L18 14 L29 20 L29 23 L18 18 L17.2 29 L20 30.5 L20 32 L16 30.8 L12 32 L12 30.5 L14.8 29 L14 18 L3 23 L3 20 L14 14 Z"
                fill="${color}" stroke="rgba(7,8,13,.9)" stroke-width="1.2"/>
        </svg>`;
    }

    function domainSatelliteSvg(color) {
        return `<svg viewBox="0 0 32 32" width="32" height="32" xmlns="http://www.w3.org/2000/svg">
            <g fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 12l8 8M20 12l-8 8"/>
                <rect x="12" y="12" width="8" height="8" rx="1.5" fill="${color}" stroke="rgba(7,8,13,.9)"/>
                <path d="M5 8l6 3-3 6-6-3zM27 18l-6-3 3-6 6 3z" fill="${color}" fill-opacity=".72"/>
                <path d="M9 23c4 3 10 3 14 0M7 26c5 4 13 4 18 0" opacity=".65"/>
            </g>
        </svg>`;
    }

    function domainRerouteSvg(color) {
        return `<svg viewBox="0 0 32 32" width="32" height="32" xmlns="http://www.w3.org/2000/svg">
            <path d="M6 22c5-12 14-12 20-6" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
            <path d="M23 10l5 6-7 2" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="7" cy="23" r="3" fill="${color}" stroke="rgba(7,8,13,.9)" stroke-width="1.4"/>
        </svg>`;
    }

    async function ensureDomainImages(map) {
        await Promise.all([
            loadDomainImage(map, 'domain-icon-flight', domainAircraftSvg('#e3b341'), 32),
            loadDomainImage(map, 'domain-icon-reroute', domainRerouteSvg('#58a6ff'), 32),
            loadDomainImage(map, 'domain-icon-satellite', domainSatelliteSvg('#a78bfa'), 32)
        ]);
    }

    function fallbackStyle() {
        return {
            version: 8,
            sources: {
                osm: {
                    type: 'raster',
                    tiles: [
                        'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
                        'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
                        'https://c.tile.openstreetmap.org/{z}/{x}/{y}.png'
                    ],
                    tileSize: 256,
                    attribution: 'OpenStreetMap'
                }
            },
            layers: [
                { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.72, 'raster-saturation': -0.45, 'raster-contrast': 0.28 } }
            ]
        };
    }

    function addLayers(map) {
        addSource(map, 'domain-tiles');
        addSource(map, 'domain-closures');
        addSource(map, 'domain-jamming');
        addSource(map, 'domain-reroutes');
        addSource(map, 'domain-reroute-lines');
        addSource(map, 'domain-sat-lines');
        addSource(map, 'domain-sat-points');

        if (!map.getLayer('domain-tiles-fill')) {
            map.addLayer({
                id: 'domain-tiles-fill', type: 'fill', source: 'domain-tiles',
                paint: {
                    'fill-color': [
                        'interpolate', ['linear'], ['get', 'score'],
                        0, 'rgba(49, 211, 95, .34)',
                        0.45, 'rgba(251, 191, 36, .34)',
                        0.75, 'rgba(255, 107, 107, .38)',
                        1, 'rgba(255, 83, 83, .44)'
                    ],
                    'fill-outline-color': [
                        'interpolate', ['linear'], ['get', 'score'],
                        0, 'rgba(49, 211, 95, .82)',
                        0.45, 'rgba(251, 191, 36, .86)',
                        1, 'rgba(255, 107, 107, .92)'
                    ]
                }
            });
        }
        if (!map.getLayer('domain-tiles-line')) {
            map.addLayer({
                id: 'domain-tiles-line', type: 'line', source: 'domain-tiles',
                paint: {
                    'line-color': [
                        'interpolate', ['linear'], ['get', 'score'],
                        0, 'rgba(49, 211, 95, .75)',
                        0.45, 'rgba(251, 191, 36, .78)',
                        1, 'rgba(255, 107, 107, .9)'
                    ],
                    'line-width': 0.65,
                    'line-opacity': 0.65
                }
            });
        }
        if (!map.getLayer('domain-jamming-fill')) {
            map.addLayer({
                id: 'domain-jamming-fill', type: 'fill', source: 'domain-jamming',
                paint: {
                    'fill-color': 'rgba(255, 193, 7, 0.08)',
                    'fill-outline-color': 'rgba(255, 193, 7, 0.50)'
                }
            });
        }
        if (!map.getLayer('domain-closures-fill')) {
            map.addLayer({
                id: 'domain-closures-fill', type: 'fill', source: 'domain-closures',
                paint: {
                    'fill-color': 'rgba(255, 107, 107, 0.11)',
                    'fill-outline-color': 'rgba(255, 107, 107, 0.62)'
                }
            });
        }
        if (!map.getLayer('domain-closures-line')) {
            map.addLayer({
                id: 'domain-closures-line', type: 'line', source: 'domain-closures',
                paint: { 'line-color': '#ff6b6b', 'line-width': 1, 'line-dasharray': [2, 1], 'line-opacity': 0.7 }
            });
        }
        if (!map.getLayer('domain-sat-lines')) {
            map.addLayer({
                id: 'domain-sat-lines', type: 'line', source: 'domain-sat-lines',
                paint: { 'line-color': '#a78bfa', 'line-width': 1.1, 'line-opacity': 0.62 }
            });
        }
        if (!map.getLayer('domain-reroute-lines')) {
            map.addLayer({
                id: 'domain-reroute-lines', type: 'line', source: 'domain-reroute-lines',
                paint: {
                    'line-color': '#58a6ff',
                    'line-width': 1.2,
                    'line-opacity': 0.62,
                    'line-dasharray': [2, 2]
                }
            });
        }
        if (!map.getLayer('domain-reroutes')) {
            map.addLayer({
                id: 'domain-reroutes', type: 'symbol', source: 'domain-reroutes',
                filter: ['==', ['get', 'kind'], 'reroute'],
                layout: {
                    'icon-image': 'domain-icon-reroute',
                    'icon-size': ['interpolate', ['linear'], ['coalesce', ['get', 'risk'], 0.3], 0, 0.55, 1, 0.85],
                    'icon-allow-overlap': true,
                    'icon-ignore-placement': true
                }
            });
        }
        if (!map.getLayer('domain-sat-points')) {
            map.addLayer({
                id: 'domain-sat-points', type: 'symbol', source: 'domain-sat-points',
                layout: {
                    'icon-image': 'domain-icon-satellite',
                    'icon-size': 0.62,
                    'icon-allow-overlap': true,
                    'icon-ignore-placement': true
                }
            });
        }
        if (!map.getLayer('domain-flights')) {
            map.addLayer({
                id: 'domain-flights', type: 'symbol', source: 'domain-reroutes',
                filter: ['==', ['get', 'kind'], 'flight'],
                layout: {
                    'icon-image': 'domain-icon-flight',
                    'icon-size': 0.62,
                    'icon-rotate': ['get', 'heading'],
                    'icon-rotation-alignment': 'map',
                    'icon-allow-overlap': true,
                    'icon-ignore-placement': true
                }
            });
        }
    }

    function bindPopups(map) {
        const layers = ['domain-tiles-fill', 'domain-closures-fill', 'domain-jamming-fill', 'domain-reroutes', 'domain-flights', 'domain-sat-points'];
        layers.forEach(layer => {
            map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
            map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
            map.on('click', layer, e => {
                const feature = e.features && e.features[0];
                if (!feature) return;
                const p = feature.properties || {};
                const title = p.title || p.name || p.label || 'Domain signal';
                const meta = [p.kind, p.status, p.count ? `${p.count} events` : '', p.risk ? `risk ${Number(p.risk).toFixed(1)}` : '']
                    .filter(Boolean).join(' · ');
                new maplibregl.Popup({ closeButton: false, offset: 10, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(`<strong>${title}</strong>${meta ? `<br><span>${meta}</span>` : ''}`)
                    .addTo(map);
            });
        });
    }

    function buildData(payload) {
        payload = payload || {};
        const tiles = [];
        const closures = (payload.closures || []).map(c => {
            return bboxFeature(c, {
            kind: 'blocked zone',
            title: val(c, 'title', 'Title', 'name', 'Name'),
            status: val(c, 'status', 'Status')
            }, 1.2);
        }).filter(Boolean);

        const jamming = (payload.jamming || []).map(t => {
            const center = centerOf(t);
            const count = val(t, 'count', 'Count') || 0;
            const intensity = number(val(t, 'intensity', 'Intensity')) ?? Math.min(1, Number(count) / 10);
            if (center) tiles.push(hexFeature(center[0], center[1], 1.15, {
                kind: 'jamming',
                score: Math.max(0.45, Math.min(0.85, intensity)),
                title: 'GPS interference tile',
                count
            }));
            return bboxFeature(t, {
                kind: 'jamming',
                title: 'GPS interference tile',
                count,
                intensity
            }, 0.7);
        }).filter(Boolean);

        const reroutes = (payload.reroutes || []).map(r => {
            const risk = number(val(r, 'risk_score', 'riskScore', 'RiskScore')) || 0.3;
            const center = centerOf(r);
            const title = val(r, 'title', 'Title') || 'Reroute signal';
            const source = String(val(r, 'source', 'Source') || '').toLowerCase();
            const isFlight = source.includes('open') || title.toLowerCase().includes('flight') || title.toLowerCase().includes('altitude');
            return pointFeature(r, {
                kind: isFlight ? 'flight' : 'reroute',
                title,
                source,
                risk
            });
        }).filter(Boolean);

        const rerouteLines = reroutes.map(f => {
            const [lon, lat] = f.geometry.coordinates;
            const risk = Number(f.properties.risk || 0.3);
            const span = 0.8 + risk * 1.2;
            return {
                type: 'Feature',
                geometry: { type: 'LineString', coordinates: [[lon - span, lat - span * 0.35], [lon, lat], [lon + span, lat + span * 0.35]] },
                properties: { ...f.properties }
            };
        });

        const satLines = [];
        const satPoints = [];
        (payload.satellites || []).forEach(s => {
            const name = val(s, 'name', 'Name', 'sat_name', 'satName', 'SatName') || 'Satellite pass';
            const points = val(s, 'points', 'Points') || [];
            const line = lineFeature(points, { kind: 'sat pass', title: name, max_el: val(s, 'max_el', 'maxEl', 'MaxEl') });
            if (line) satLines.push(line);
            const point = pointFeature(points[0] || s, { kind: 'sat pass', title: name });
            if (point) satPoints.push(point);
            const center = centerOf(points[0] || s);
        });

        return { tiles: tiles.filter(Boolean), closures, jamming, reroutes, rerouteLines, satLines, satPoints };
    }

    function setData(map, payload) {
        const data = buildData(payload);
        const pairs = [
            ['domain-tiles', data.tiles],
            ['domain-closures', data.closures],
            ['domain-jamming', data.jamming],
            ['domain-reroutes', data.reroutes],
            ['domain-reroute-lines', data.rerouteLines],
            ['domain-sat-lines', data.satLines],
            ['domain-sat-points', data.satPoints]
        ];
        pairs.forEach(([id, features]) => {
            const src = map.getSource(id);
            if (src) src.setData({ type: 'FeatureCollection', features });
        });
        fitToData(map, [...data.tiles, ...data.closures, ...data.reroutes, ...data.satPoints]);
    }

    function fitToData(map, features) {
        if (map.__viDomainFitted || !features || !features.length || !window.maplibregl) return;
        const bounds = new maplibregl.LngLatBounds();
        let count = 0;
        const walk = coords => {
            if (!coords) return;
            if (typeof coords[0] === 'number' && typeof coords[1] === 'number') {
                bounds.extend(coords);
                count++;
                return;
            }
            coords.forEach(walk);
        };
        features.forEach(f => walk(f.geometry?.coordinates));
        if (count > 0) {
            map.__viDomainFitted = true;
            try { map.fitBounds(bounds, { padding: 56, duration: 500, maxZoom: 5.5 }); } catch (_) {}
        }
    }

    function init(containerId) {
        if (!window.maplibregl) {
            console.warn('[viDomainGlobe] MapLibre is not loaded');
            return false;
        }
        const container = document.getElementById(containerId);
        if (!container) return false;
        const rect = container.getBoundingClientRect();
        if ((rect.width < 40 || rect.height < 40) && (retries[containerId] || 0) < 12) {
            retries[containerId] = (retries[containerId] || 0) + 1;
            window.setTimeout(() => init(containerId), 120);
            return false;
        }
        retries[containerId] = 0;
        destroy(containerId);

        const map = new maplibregl.Map({
            container,
            style: defaultStyleUrl || fallbackStyle(),
            center: [20, 28],
            zoom: 1.35,
            bearing: -6,
            pitch: 18,
            attributionControl: false,
            renderWorldCopies: false,
            interactive: true,
            preserveDrawingBuffer: false
        });
        maps[containerId] = map;
        map.addControl(new maplibregl.NavigationControl({ showCompass: true, showZoom: true }), 'bottom-right');
        map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 90 }), 'bottom-left');
        window.setTimeout(() => { try { map.resize(); } catch (_) {} }, 50);
        window.setTimeout(() => { try { map.resize(); } catch (_) {} }, 300);

        if (typeof ResizeObserver !== 'undefined') {
            observers[containerId] = new ResizeObserver(() => {
                try { map.resize(); } catch (_) {}
            });
            observers[containerId].observe(container);
        }

        map.on('load', () => {
            try {
                map.getCanvas().classList.add('vi-domain-map-ready');
                ensureDomainImages(map).then(() => {
                    addLayers(map);
                    bindPopups(map);
                    setData(map, pending[containerId] || {});
                    map.resize();
                });
            } catch (e) {
                console.warn('[viDomainGlobe] load failed', e);
            }
        });
        map.on('error', e => {
            const message = e.error?.message || String(e);
            console.warn('[viDomainGlobe]', message);
            if (!map.__viFallbackApplied && /style|sprite|glyph|fetch|load|Failed/i.test(message)) {
                map.__viFallbackApplied = true;
                try { map.setStyle(fallbackStyle()); } catch (_) {}
            }
        });
        return true;
    }

    function update(containerId, payload) {
        pending[containerId] = payload || {};
        const map = maps[containerId];
        if (!map || !map.loaded()) return false;
        ensureDomainImages(map).then(() => {
            addLayers(map);
            setData(map, payload);
        });
        return true;
    }

    function destroy(containerId) {
        if (observers[containerId]) {
            try { observers[containerId].disconnect(); } catch (_) {}
            delete observers[containerId];
        }
        if (maps[containerId]) {
            try { maps[containerId].remove(); } catch (_) {}
            delete maps[containerId];
        }
        delete pending[containerId];
    }

    return { init, update, destroy };
})();
