/**
 * oracle-interop.js
 * Oracle page: regional escalation choropleth + Cortex community network
 */

const ESC_COLOURS = {
    CRITICAL: { fill: 'rgba(248,81,73,0.55)', stroke: '#f85149' },
    HIGH:     { fill: 'rgba(210,153,34,0.45)', stroke: '#d29922' },
    ELEVATED: { fill: 'rgba(88,166,255,0.30)', stroke: '#58a6ff' },
    LOW:      { fill: 'rgba(63,185,80,0.15)',  stroke: '#3fb950' },
};

// Simple region to approximate centre coordinates mapping
const REGION_CENTRES = {
    MENA:               { lat: 26,  lon: 45,  label: 'MENA' },
    EUROPE:             { lat: 50,  lon: 15,  label: 'EUROPE' },
    INDO_PACIFIC:       { lat: 10,  lon: 125, label: 'INDO-PAC' },
    EASTERN_EUROPE:     { lat: 50,  lon: 30,  label: 'E.EUROPE' },
    SUB_SAHARAN_AFRICA: { lat: 5,   lon: 25,  label: 'SSA' },
    LATIN_AMERICA:      { lat: -15, lon: -60, label: 'LATAM' },
    SOUTH_ASIA:         { lat: 25,  lon: 78,  label: 'S.ASIA' },
};

window.viOracle = (function () {
    let _map = null;

    function renderChoropleth(containerId, scores) {
        const container = document.getElementById(containerId);
        if (!container) return;

        // Remove loading placeholder
        const placeholder = document.getElementById('oracle-map-loading');
        if (placeholder) placeholder.style.display = 'none';

        // Init MapLibre GL map inside oracle container
        if (!_map) {
            if (!window.maplibregl) {
                container.innerHTML = '<div style="padding:40px;text-align:center;color:#3d444d;font-size:10px;font-family:monospace;">MAPLIBRE NOT LOADED</div>';
                return;
            }
            _map = new maplibregl.Map({
                container: containerId,
                style: {
                    version: 8,
                    sources: {
                        'carto': {
                            type: 'raster',
                            tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
                            tileSize: 256,
                            attribution: 'CartoDB',
                        }
                    },
                    layers: [{ id: 'carto', type: 'raster', source: 'carto' }],
                },
                center: [20, 20],
                zoom: 1.5,
                attributionControl: false,
                interactive: true,
            });
            _map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
        }

        // Wait for style load then add bubbles
        const addBubbles = () => {
            // Remove old layers
            ['oracle-bubbles', 'oracle-labels'].forEach(id => {
                if (_map.getLayer(id)) _map.removeLayer(id);
                if (_map.getSource(id)) _map.removeSource(id);
            });

            const geojson = {
                type: 'FeatureCollection',
                features: (scores || []).map(s => {
                    const centre = REGION_CENTRES[s.region] || { lat: 0, lon: 0, label: s.region };
                    const col    = ESC_COLOURS[s.risk] || ESC_COLOURS.LOW;
                    return {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [centre.lon, centre.lat] },
                        properties: {
                            region:   centre.label,
                            score:    s.score,
                            risk:     s.risk,
                            drivers:  (s.drivers || []).slice(0, 2).join(', '),
                            fillColour:   col.fill,
                            strokeColour: col.stroke,
                            radius: 20 + (s.score * 60),
                        }
                    };
                })
            };

            _map.addSource('oracle-bubbles', { type: 'geojson', data: geojson });

            _map.addLayer({
                id:     'oracle-bubbles',
                type:   'circle',
                source: 'oracle-bubbles',
                paint: {
                    'circle-radius':       ['get', 'radius'],
                    'circle-color':        ['get', 'fillColour'],
                    'circle-stroke-width': 1.5,
                    'circle-stroke-color': ['get', 'strokeColour'],
                    'circle-opacity':      0.85,
                }
            });

            _map.addLayer({
                id:     'oracle-labels',
                type:   'symbol',
                source: 'oracle-bubbles',
                layout: {
                    'text-field': ['concat', ['get', 'region'], '\n', ['to-string', ['round', ['*', ['get', 'score'], 100]]], '%'],
                    'text-size':  10,
                    'text-font':  ['Open Sans Bold', 'Arial Unicode MS Bold'],
                    'text-anchor': 'center',
                    'text-allow-overlap': true,
                },
                paint: {
                    'text-color': '#c9d1d9',
                    'text-halo-color': 'rgba(0,0,0,0.6)',
                    'text-halo-width': 1,
                }
            });

            // Popup on click
            _map.on('click', 'oracle-bubbles', (e) => {
                const p = e.features[0].properties;
                new maplibregl.Popup({ closeButton: false, className: 'vi-popup' })
                    .setLngLat(e.lngLat)
                    .setHTML(`<div style="font-family:monospace;font-size:10px;color:#c9d1d9;">
                        <b>${p.region}</b><br>
                        SCORE: ${(p.score * 100).toFixed(0)}% | ${p.risk}<br>
                        ${p.drivers ? 'UP ' + p.drivers : ''}
                    </div>`)
                    .addTo(_map);
            });
            _map.on('mouseenter', 'oracle-bubbles', () => { _map.getCanvas().style.cursor = 'pointer'; });
            _map.on('mouseleave', 'oracle-bubbles', () => { _map.getCanvas().style.cursor = ''; });
        };

        if (_map.isStyleLoaded()) {
            addBubbles();
        } else {
            _map.once('load', addBubbles);
        }
    }

    return { renderChoropleth };
})();

window.viCortex = (function () {
    let _network = null;

    // 12 distinct community colours
    const COMM_COLOURS = [
        '#58a6ff','#3fb950','#f85149','#d29922','#a78bfa',
        '#fb7185','#34d399','#60a5fa','#fbbf24','#e879f9',
        '#22d3ee','#f97316',
    ];

    function renderCommunityGraph(containerId, nodes, edges) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (_network) { _network.destroy(); _network = null; }

        if (!nodes || nodes.length === 0) {
            container.innerHTML = '<div style="padding:40px;text-align:center;color:#3d444d;font-size:10px;font-family:monospace;">NO COMMUNITY DATA | RUN GRAPH PRECOMPUTE</div>';
            return;
        }

        const visNodes = new vis.DataSet(nodes.map(n => ({
            id:    n.id,
            label: n.label || n.id,
            color: {
                background: COMM_COLOURS[(n.group || 0) % COMM_COLOURS.length] + '33',
                border:     COMM_COLOURS[(n.group || 0) % COMM_COLOURS.length],
                highlight:  { background: COMM_COLOURS[(n.group || 0) % COMM_COLOURS.length] + '55', border: '#fff' },
            },
            font:  { color: '#c9d1d9', size: 10, face: 'JetBrains Mono' },
            shape: 'dot',
            size:  8 + Math.min(edges.filter(e => e.from === n.id || e.to === n.id).length * 2, 20),
        })));

        const visEdges = new vis.DataSet(edges.map(e => ({
            from:  e.from,
            to:    e.to,
            value: e.weight || 1,
            color: { color: 'rgba(88,166,255,0.18)', highlight: 'rgba(88,166,255,0.55)' },
            width: Math.min(1 + (e.weight || 1) * 0.3, 4),
        })));

        const options = {
            nodes: { borderWidth: 1.5 },
            edges: { smooth: { type: 'continuous', roundness: 0.2 } },
            physics: {
                stabilization: { iterations: 80 },
                forceAtlas2Based: { gravitationalConstant: -40, centralGravity: 0.005, springLength: 80 },
                solver: 'forceAtlas2Based',
            },
            interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true },
            layout: { improvedLayout: false },
        };

        _network = new vis.Network(container, { nodes: visNodes, edges: visEdges }, options);
    }

    return { renderCommunityGraph };
})();

