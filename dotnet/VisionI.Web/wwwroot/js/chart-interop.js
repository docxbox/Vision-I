// Vision-I — Chart.js interop v2
// Theme-aware chart rendering for Blazor interop.
window.viCharts = {
    _instances: {},

    _getThemeColors: function () {
        var isDark = (document.documentElement.getAttribute('data-theme') || 'dark') !== 'light';
        return {
            gridColor: isDark ? 'rgba(141,174,208,.08)' : 'rgba(15,23,42,.06)',
            tickColor: isDark ? '#5f7288' : '#94a3b8',
            legendColor: isDark ? '#9fb3c9' : '#64748b',
            tooltipBg: isDark ? '#0b1624' : '#ffffff',
            tooltipBorder: isDark ? '#1a3147' : '#e2e8f0',
            tooltipTitle: isDark ? '#9fb3c9' : '#64748b',
            tooltipBody: isDark ? '#edf6ff' : '#0f172a',
            centerText: isDark ? '#edf6ff' : '#0f172a',
            centerSub: isDark ? '#5f7288' : '#94a3b8',
            borderBg: isDark ? '#0b1624' : '#ffffff'
        };
    },

    // Render or update a sentiment timeline line chart
    renderSentimentChart: function (canvasId, labels, positive, neutral, negative) {
        if (this._instances[canvasId]) {
            this._instances[canvasId].destroy();
        }

        var canvas = document.getElementById(canvasId);
        if (!canvas) return;

        var t = this._getThemeColors();
        var ctx = canvas.getContext('2d');

        this._instances[canvasId] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Positive',
                        data: positive,
                        borderColor: '#2dd4bf',
                        backgroundColor: 'rgba(45,212,191,0.06)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        borderWidth: 1.5,
                    },
                    {
                        label: 'Neutral',
                        data: neutral,
                        borderColor: '#6ec1ff',
                        backgroundColor: 'rgba(110,193,255,0.04)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        borderWidth: 1.5,
                    },
                    {
                        label: 'Negative',
                        data: negative,
                        borderColor: '#ef6b73',
                        backgroundColor: 'rgba(239,107,115,0.06)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        borderWidth: 1.5,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 500, easing: 'easeOutCubic' },
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom',
                        labels: {
                            color: t.legendColor,
                            font: { size: 10, family: 'Space Grotesk, sans-serif' },
                            boxWidth: 10,
                            padding: 10,
                            usePointStyle: true,
                            pointStyle: 'circle',
                        },
                    },
                    tooltip: {
                        backgroundColor: t.tooltipBg,
                        borderColor: t.tooltipBorder,
                        borderWidth: 1,
                        titleColor: t.tooltipTitle,
                        bodyColor: t.tooltipBody,
                        titleFont: { size: 10, family: 'Space Grotesk, sans-serif' },
                        bodyFont: { size: 11, family: 'IBM Plex Mono, monospace' },
                        padding: 10,
                        cornerRadius: 6,
                        displayColors: true,
                        boxPadding: 4,
                    },
                },
                scales: {
                    x: {
                        ticks: {
                            color: t.tickColor,
                            font: { size: 9, family: 'IBM Plex Mono, monospace' },
                            maxTicksLimit: 8,
                        },
                        grid: { color: t.gridColor, drawBorder: false },
                    },
                    y: {
                        ticks: {
                            color: t.tickColor,
                            font: { size: 9, family: 'IBM Plex Mono, monospace' },
                        },
                        grid: { color: t.gridColor, drawBorder: false },
                        beginAtZero: true,
                    },
                },
            },
        });
    },

    // Render event type distribution doughnut chart
    renderTypeChart: function (canvasId, labels, counts) {
        if (this._instances[canvasId]) {
            this._instances[canvasId].destroy();
        }
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;

        var t = this._getThemeColors();
        var colors = {
            'disaster': '#ef6b73',
            'news': '#6ec1ff',
            'market': '#2dd4bf',
            'transport': '#fb923c',
            'social': '#a78bfa',
            'video': '#f472b6',
            'weather': '#60a5fa',
            'health': '#86efac',
            'composite': '#c084fc',
            'transport_anomaly': '#fb923c',
        };
        var bgColors = labels.map(function (l) { return colors[l] || '#5f7288'; });

        this._instances[canvasId] = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: counts,
                    backgroundColor: bgColors,
                    borderColor: t.borderBg,
                    borderWidth: 2,
                    hoverOffset: 6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '68%',
                animation: { animateRotate: true, duration: 600, easing: 'easeOutCubic' },
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            color: t.legendColor,
                            font: { size: 10, family: 'Space Grotesk, sans-serif' },
                            boxWidth: 10,
                            padding: 8,
                            usePointStyle: true,
                            pointStyle: 'circle',
                        },
                    },
                    tooltip: {
                        backgroundColor: t.tooltipBg,
                        borderColor: t.tooltipBorder,
                        borderWidth: 1,
                        bodyColor: t.tooltipBody,
                        bodyFont: { size: 11, family: 'IBM Plex Mono, monospace' },
                        padding: 10,
                        cornerRadius: 6,
                    },
                },
            },
        });
    },

    // Render confidence distribution doughnut chart
    renderConfidenceChart: function (canvasId, high, medium, low) {
        if (this._instances[canvasId]) {
            this._instances[canvasId].destroy();
        }
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;

        var t = this._getThemeColors();
        var total = high + medium + low;
        var centerTextPlugin = {
            id: 'viCenterText_' + canvasId,
            afterDraw: function (chart) {
                var tc = chart.canvas.closest('[data-theme]');
                var isDark = !tc || tc.getAttribute('data-theme') !== 'light';
                var ctx = chart.ctx;
                var w = chart.chartArea.right - chart.chartArea.left;
                var cx = chart.chartArea.left + w / 2;
                var cy = chart.chartArea.top + (chart.chartArea.bottom - chart.chartArea.top) / 2;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = isDark ? '#edf6ff' : '#0f172a';
                ctx.font = 'bold 18px Space Grotesk, sans-serif';
                ctx.fillText(total.toString(), cx, cy - 6);
                ctx.fillStyle = isDark ? '#5f7288' : '#94a3b8';
                ctx.font = '9px Space Grotesk, sans-serif';
                ctx.fillText('total', cx, cy + 12);
                ctx.restore();
            }
        };

        this._instances[canvasId] = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: ['High', 'Medium', 'Low'],
                datasets: [{
                    data: [high, medium, low],
                    backgroundColor: ['#2dd4bf', '#f7b955', '#ef6b73'],
                    borderColor: t.borderBg,
                    borderWidth: 2,
                    hoverOffset: 6,
                }],
            },
            plugins: [centerTextPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '68%',
                animation: { duration: 700, easing: 'easeOutCubic' },
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            color: t.legendColor,
                            font: { size: 10, family: 'Space Grotesk, sans-serif' },
                            boxWidth: 10,
                            padding: 8,
                            usePointStyle: true,
                            pointStyle: 'circle',
                        },
                    },
                    tooltip: {
                        backgroundColor: t.tooltipBg,
                        borderColor: t.tooltipBorder,
                        borderWidth: 1,
                        bodyColor: t.tooltipBody,
                        bodyFont: { size: 11, family: 'IBM Plex Mono, monospace' },
                        padding: 10,
                        cornerRadius: 6,
                    },
                },
            },
        });
    },

    destroy: function (canvasId) {
        if (this._instances[canvasId]) {
            this._instances[canvasId].destroy();
            delete this._instances[canvasId];
        }
    },

    // Destroy all chart instances
    destroyAll: function () {
        var keys = Object.keys(this._instances);
        for (var i = 0; i < keys.length; i++) {
            this._instances[keys[i]].destroy();
        }
        this._instances = {};
    }
};
