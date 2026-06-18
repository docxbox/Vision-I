window.viGraph = {
    state: null,

    render: function (containerId, nodesArray, edgesArray, dotNetRef) {
        const container = document.getElementById(containerId);
        if (!container || typeof cytoscape === "undefined") return;

        this.destroy();
        const elements = this._toElements(nodesArray, edgesArray);

        const cy = cytoscape({
            container,
            elements,
            wheelSensitivity: 0.18,
            minZoom: 0.35,
            maxZoom: 2.6,
            motionBlur: true,
            selectionType: "single",
            style: [
                {
                    selector: "core",
                    style: {
                        "background-color": "#08111a",
                        "active-bg-color": "#43b0ff",
                        "active-bg-opacity": 0.14
                    }
                },
                {
                    selector: "node",
                    style: {
                        label: "data(label)",
                        color: "#d7dee7",
                        "font-family": "JetBrains Mono, monospace",
                        "font-size": 9,
                        "font-weight": 600,
                        "text-wrap": "wrap",
                        "text-max-width": 112,
                        "text-valign": "bottom",
                        "text-margin-y": 12,
                        "text-halign": "center",
                        "background-color": "data(fill)",
                        "border-color": "data(stroke)",
                        "border-width": 2,
                        width: "mapData(value, 1, 80, 24, 54)",
                        height: "mapData(value, 1, 80, 24, 54)",
                        "overlay-opacity": 0,
                        "shadow-blur": 18,
                        "shadow-color": "data(glow)",
                        "shadow-opacity": 0.45
                    }
                },
                {
                    selector: "node:selected",
                    style: {
                        "border-width": 4,
                        "border-color": "#f2c94c",
                        "shadow-opacity": 0.8,
                        "shadow-blur": 24
                    }
                },
                {
                    selector: "edge",
                    style: {
                        label: "data(label)",
                        color: "#7d8ba1",
                        "font-family": "JetBrains Mono, monospace",
                        "font-size": 9,
                        width: "mapData(weight, 1, 10, 1.3, 4.6)",
                        "line-color": "rgba(86, 110, 140, 0.72)",
                        "target-arrow-color": "rgba(86, 110, 140, 0.72)",
                        "curve-style": "bezier",
                        "target-arrow-shape": "triangle-backcurve",
                        "arrow-scale": 0.72,
                        "text-background-color": "#08111a",
                        "text-background-opacity": 0.82,
                        "text-background-padding": 2,
                        "text-rotation": "autorotate"
                    }
                },
                {
                    selector: ".faded",
                    style: {
                        opacity: 0.15,
                        "text-opacity": 0.08
                    }
                }
            ],
            layout: {
                name: "cose",
                animate: false,
                fit: true,
                padding: 72,
                nodeRepulsion: 90000,
                idealEdgeLength: 110,
                edgeElasticity: 48,
                gravity: 0.32,
                numIter: 600
            }
        });

        const state = {
            cy,
            dotNetRef: dotNetRef || null,
            selectedNodeId: null,
            lastTap: { id: null, at: 0 }
        };
        this.state = state;

        cy.on("tap", "node", (evt) => {
            const node = evt.target;
            const id = node.id();
            const now = Date.now();
            const last = state.lastTap;
            const isDouble = last.id === id && now - last.at < 320;

            state.selectedNodeId = id;
            this._spotlight(node);

            if (state.dotNetRef) {
                state.dotNetRef.invokeMethodAsync("OnNodeSelected", String(id)).catch(() => { });
                if (isDouble) {
                    state.dotNetRef.invokeMethodAsync("OnNodeDoubleClick", String(id)).catch(() => { });
                }
            }

            state.lastTap = { id, at: now };
        });

        cy.on("tap", (evt) => {
            if (evt.target === cy) {
                cy.elements().removeClass("faded");
                if (state.dotNetRef) {
                    state.dotNetRef.invokeMethodAsync("OnNodeDeselected").catch(() => { });
                }
            }
        });

        cy.on("mouseover", "node", (evt) => {
            const node = evt.target;
            node.style("shadow-opacity", 0.8);
        });

        cy.on("mouseout", "node", (evt) => {
            const node = evt.target;
            if (node.id() !== state.selectedNodeId) {
                node.style("shadow-opacity", 0.45);
            }
        });
    },

    expandNodes: function (newNodes, newEdges) {
        if (!this.state || !this.state.cy) return;
        const cy = this.state.cy;
        const elements = this._toElements(newNodes, newEdges).filter((el) => !cy.getElementById(el.data.id).length);

        if (elements.length) {
            cy.add(elements);
            cy.layout({
                name: "cose",
                animate: false,
                fit: false,
                padding: 48,
                nodeRepulsion: 90000,
                idealEdgeLength: 110,
                gravity: 0.32,
                numIter: 420
            }).run();
        }
    },

    focusNode: function (nodeId, scale) {
        if (!this.state || !this.state.cy || !nodeId) return;
        const node = this.state.cy.getElementById(nodeId);
        if (!node.length) return;
        this.state.selectedNodeId = nodeId;
        this._spotlight(node);
        this.state.cy.animate({
            fit: { eles: node.closedNeighborhood(), padding: 120 },
            zoom: Math.max(0.55, Math.min(scale || 1.08, 1.8)),
            center: { eles: node }
        }, {
            duration: 320
        });
    },

    fit: function () {
        if (!this.state || !this.state.cy) return;
        this.state.cy.elements().removeClass("faded");
        this.state.cy.fit(this.state.cy.elements(), 72);
    },

    updateNodes: function (updatedNodes) {
        if (!this.state || !this.state.cy || !Array.isArray(updatedNodes)) return;
        const cy = this.state.cy;
        updatedNodes.forEach((node) => {
            const target = cy.getElementById(node.id);
            if (!target.length) return;
            const palette = this._palette(node.group);
            target.data({
                label: node.label,
                value: node.value || 1,
                fill: palette.fill,
                stroke: palette.stroke,
                glow: palette.glow
            });
        });
    },

    exportGraph: function () {
        if (!this.state || !this.state.cy) return;
        try {
            const png = this.state.cy.png({
                bg: "#08111a",
                full: true,
                scale: 2
            });
            const a = document.createElement("a");
            a.href = png;
            a.download = "vision-i-graph.png";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (err) {
            console.warn("[viGraph] export failed", err);
        }
    },

    destroy: function () {
        if (!this.state || !this.state.cy) return;
        try {
            this.state.cy.destroy();
        } catch (_) {
        }
        this.state = null;
    },

    _spotlight: function (node) {
        if (!this.state || !this.state.cy || !node) return;
        const cy = this.state.cy;
        cy.elements().addClass("faded");
        node.closedNeighborhood().removeClass("faded");
        node.removeClass("faded");
    },

    _toElements: function (nodesArray, edgesArray) {
        const elements = [];
        (Array.isArray(nodesArray) ? nodesArray : []).forEach((node) => {
            const palette = this._palette(node.group);
            elements.push({
                data: {
                    id: String(node.id),
                    label: node.label || node.id || "",
                    group: node.group || "default",
                    value: Math.max(Number(node.value || 1), 1),
                    fill: palette.fill,
                    stroke: palette.stroke,
                    glow: palette.glow
                }
            });
        });

        (Array.isArray(edgesArray) ? edgesArray : []).forEach((edge, index) => {
            const id = [edge.from, edge.to, edge.label || "", index].join("|");
            elements.push({
                data: {
                    id,
                    source: String(edge.from),
                    target: String(edge.to),
                    label: edge.label || "",
                    weight: Math.max(Number(edge.value || 1), 1)
                }
            });
        });
        return elements;
    },

    _palette: function (group) {
        const key = String(group || "default").toLowerCase();
        const palettes = {
            actor:        { fill: "#0f2238", stroke: "#58a6ff", glow: "rgba(88,166,255,0.44)" },
            person:       { fill: "#0f2238", stroke: "#58a6ff", glow: "rgba(88,166,255,0.44)" },
            event:        { fill: "#112315", stroke: "#3fb950", glow: "rgba(63,185,80,0.38)" },
            organization: { fill: "#221233", stroke: "#a371f7", glow: "rgba(163,113,247,0.4)" },
            location:     { fill: "#2b220f", stroke: "#f2c94c", glow: "rgba(242,201,76,0.38)" },
            theme:        { fill: "#102328", stroke: "#20d5c4", glow: "rgba(32,213,196,0.4)" },
            signal:       { fill: "#151d2a", stroke: "#ff8f4d", glow: "rgba(255,143,77,0.38)" },
            narrative:    { fill: "#152233", stroke: "#6ec6ff", glow: "rgba(110,198,255,0.34)" },
            default:      { fill: "#171f2a", stroke: "#6e7681", glow: "rgba(110,118,129,0.32)" }
        };
        return palettes[key] || palettes.default;
    }
};
