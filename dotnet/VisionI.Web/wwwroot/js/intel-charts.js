// Chart.js initialization for Vision-I Dashboard
window.intelCharts = {
    charts: {},

    renderSentimentChart: function(containerId, labels, values, type = 'line') {
        const ctx = document.getElementById(containerId);
        if (!ctx) return;

        // Destroy existing chart
        if (this.charts[containerId]) {
            this.charts[containerId].destroy();
        }

        const isDark = true;
        const gridColor = 'rgba(255, 255, 255, 0.05)';
        const textColor = '#6e7681';

        const dataset = {
            label: 'Sentiment',
            data: values,
            borderColor: (context) => {
                const value = context.raw;
                return value > 0.1 ? '#3fb950' : value < -0.1 ? '#f85149' : '#58a6ff';
            },
            backgroundColor: (context) => {
                const value = context.raw;
                const color = value > 0.1 ? 'rgba(63, 185, 80, 0.1)' :
                              value < -0.1 ? 'rgba(248, 81, 73, 0.1)' : 'rgba(88, 166, 255, 0.1)';
                return color;
            },
            borderWidth: 2,
            fill: type === 'line',
            tension: 0.4,
            pointRadius: 3,
            pointHoverRadius: 6,
            pointBackgroundColor: '#0a0d12',
            pointBorderWidth: 2,
        };

        const config = {
            type: type,
            data: {
                labels: labels,
                datasets: [dataset]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: '#14181f',
                        titleColor: '#c9d1d9',
                        bodyColor: '#c9d1d9',
                        borderColor: '#1e2732',
                        borderWidth: 1,
                        padding: 12,
                        titleFont: { size: 12, weight: '600' },
                        bodyFont: { size: 11 },
                        callbacks: {
                            label: function(context) {
                                return `Sentiment: ${context.parsed.y.toFixed(3)}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {
                            color: gridColor,
                            drawBorder: false,
                        },
                        ticks: {
                            color: textColor,
                            font: { size: 10 },
                            maxTicksLimit: 8
                        }
                    },
                    y: {
                        grid: {
                            color: gridColor,
                            drawBorder: false,
                        },
                        ticks: {
                            color: textColor,
                            font: { size: 10 },
                            callback: function(value) {
                                return value.toFixed(1);
                            }
                        },
                        suggestedMin: -1,
                        suggestedMax: 1
                    }
                }
            }
        };

        this.charts[containerId] = new Chart(ctx, config);
    },

    updateSentimentChart: function(containerId, labels, values) {
        const chart = this.charts[containerId];
        if (!chart) return;

        chart.data.labels = labels;
        chart.data.datasets[0].data = values;
        chart.update('none');
    },

    exportChart: function(containerId) {
        const chart = this.charts[containerId];
        if (!chart) return;

        const link = document.createElement('a');
        link.download = `sentiment-chart-${new Date().toISOString().split('T')[0]}.png`;
        link.href = chart.toBase64Image();
        link.click();
    },

    renderTimelineChart: function(containerId, labels, datasets) {
        const ctx = document.getElementById(containerId);
        if (!ctx) return;

        if (this.charts[containerId]) {
            this.charts[containerId].destroy();
        }

        const config = {
            type: 'line',
            data: {
                labels: labels,
                datasets: datasets.map((ds, i) => ({
                    label: ds.label,
                    data: ds.data,
                    borderColor: ds.color || '#58a6ff',
                    backgroundColor: ds.color ? `${ds.color}20` : 'rgba(88, 166, 255, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            color: '#6e7681',
                            font: { size: 11 },
                            boxWidth: 12,
                        }
                    },
                    tooltip: {
                        backgroundColor: '#14181f',
                        borderColor: '#1e2732',
                        borderWidth: 1,
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.05)', drawBorder: false },
                        ticks: { color: '#6e7681', font: { size: 10 } }
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.05)', drawBorder: false },
                        ticks: { color: '#6e7681', font: { size: 10 } }
                    }
                }
            }
        };

        this.charts[containerId] = new Chart(ctx, config);
    },

    destroy: function(containerId) {
        if (this.charts[containerId]) {
            this.charts[containerId].destroy();
            delete this.charts[containerId];
        }
    }
};

// Keyboard shortcuts
window.intelApp = {
    shortcuts: null,

    registerShortcuts: function(dotnetRef) {
        this.shortcuts = dotnetRef;

        document.addEventListener('keydown', (e) => {
            // Cmd/Ctrl + K for search
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                dotnetRef.invokeMethodAsync('OpenSearch');
            }

            // Escape to close
            if (e.key === 'Escape') {
                dotnetRef.invokeMethodAsync('CloseSearch');
            }
        });
    }
};

// Graph visualization with D3
window.intelGraph = {
    svg: null,
    simulation: null,
    nodes: [],
    links: [],

    init: function(containerId, width, height) {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = '';

        this.svg = d3.select(`#${containerId}`)
            .append('svg')
            .attr('width', width)
            .attr('height', height)
            .attr('viewBox', [0, 0, width, height]);

        // Zoom behavior
        const zoom = d3.zoom()
            .scaleExtent([0.1, 4])
            .on('zoom', (e) => {
                this.svg.select('g').attr('transform', e.transform);
            });

        this.svg.call(zoom);

        // Group for zoomable content
        this.svg.append('g');

        // Force simulation
        this.simulation = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(100))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(40));

        return true;
    },

    render: function(nodes, links) {
        if (!this.svg) return;

        const g = this.svg.select('g');

        // Links
        const link = g.selectAll('.link')
            .data(links)
            .join('line')
            .attr('class', 'link')
            .attr('stroke', '#2d3a4a')
            .attr('stroke-width', 1.5)
            .attr('stroke-opacity', 0.6);

        // Nodes
        const node = g.selectAll('.node')
            .data(nodes)
            .join('g')
            .attr('class', 'node')
            .call(d3.drag()
                .on('start', (e, d) => {
                    if (!e.active) this.simulation.alphaTarget(0.3).restart();
                    d.fx = d.x;
                    d.fy = d.y;
                })
                .on('drag', (e, d) => {
                    d.fx = e.x;
                    d.fy = e.y;
                })
                .on('end', (e, d) => {
                    if (!e.active) this.simulation.alphaTarget(0);
                    d.fx = null;
                    d.fy = null;
                }));

        // Node circles
        node.append('circle')
            .attr('r', d => d.radius || 20)
            .attr('fill', d => this.getNodeColor(d.type))
            .attr('stroke', '#1e2732')
            .attr('stroke-width', 2)
            .style('cursor', 'pointer');

        // Node labels
        node.append('text')
            .text(d => d.label)
            .attr('text-anchor', 'middle')
            .attr('dy', 35)
            .attr('fill', '#c9d1d9')
            .attr('font-size', '11px')
            .attr('font-weight', '500');

        // Update simulation
        this.simulation
            .nodes(nodes)
            .on('tick', () => {
                link
                    .attr('x1', d => d.source.x)
                    .attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x)
                    .attr('y2', d => d.target.y);

                node.attr('transform', d => `translate(${d.x},${d.y})`);
            });

        this.simulation.force('link').links(links);
        this.simulation.alpha(1).restart();

        // Click handlers
        node.on('click', (e, d) => {
            if (window.dotnetGraph) {
                window.dotnetGraph.invokeMethodAsync('OnNodeClick', d.id);
            }
        });
    },

    getNodeColor: function(type) {
        const colors = {
            'actor': '#3fb950',
            'event': '#58a6ff',
            'location': '#f2c94c',
            'organization': '#a371f7',
            'default': '#6e7681'
        };
        return colors[type] || colors['default'];
    },

    destroy: function() {
        if (this.simulation) {
            this.simulation.stop();
        }
        if (this.svg) {
            this.svg.remove();
        }
        this.svg = null;
        this.simulation = null;
    }
};

// Map initialization
window.intelMap = {
    map: null,
    markers: [],

    init: function(containerId, center, zoom) {
        const container = document.getElementById(containerId);
        if (!container) return;

        this.map = L.map(containerId, {
            center: center || [20, 0],
            zoom: zoom || 3,
            minZoom: 2,
            maxZoom: 18,
            zoomControl: false,
            attributionControl: false
        });

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            subdomains: 'abcd',
            maxZoom: 19
        }).addTo(this.map);

        L.control.zoom({ position: 'bottomright' }).addTo(this.map);

        return true;
    },

    addMarkers: function(events) {
        if (!this.map) return;

        // Clear existing markers
        this.markers.forEach(m => this.map.removeLayer(m));
        this.markers = [];

        events.forEach(event => {
            if (!event.lat || !event.lng) return;

            const color = this.getSentimentColor(event.sentiment);
            const marker = L.circleMarker([event.lat, event.lng], {
                radius: 8 + (event.weight || 1) * 2,
                fillColor: color,
                color: '#1e2732',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.8
            }).addTo(this.map);

            marker.bindPopup(`
                <div style="font-family: Inter, sans-serif; min-width: 200px;">
                    <div style="font-size: 13px; font-weight: 600; margin-bottom: 4px; color: #c9d1d9;">${event.title}</div>
                    <div style="font-size: 11px; color: #6e7681;">${event.location || 'Unknown location'}</div>
                    <div style="font-size: 11px; color: #6e7681; margin-top: 4px;">${event.timestamp}</div>
                </div>
            `);

            marker.on('click', () => {
                if (window.dotnetMap) {
                    window.dotnetMap.invokeMethodAsync('OnMarkerClick', event.id);
                }
            });

            this.markers.push(marker);
        });
    },

    getSentimentColor: function(sentiment) {
        if (sentiment > 0.2) return '#3fb950';
        if (sentiment < -0.2) return '#f85149';
        return '#f2c94c';
    },

    setView: function(center, zoom) {
        if (this.map) {
            this.map.setView(center, zoom);
        }
    },

    destroy: function() {
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
        this.markers = [];
    }
};
