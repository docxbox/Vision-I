// Vision-I — Particle canvas interop
// Network constellation effect for auth pages
window.viParticles = {
    _anim: null,

    init: function (canvasId) {
        var c = document.getElementById(canvasId);
        if (!c) return;
        var ctx = c.getContext('2d');
        var particles = [];
        var N = 40;

        function resize() { c.width = c.offsetWidth; c.height = c.offsetHeight; }
        resize();
        window.addEventListener('resize', resize);

        for (var i = 0; i < N; i++) {
            particles.push({
                x: Math.random() * c.width,
                y: Math.random() * c.height,
                vx: (Math.random() - 0.5) * 0.25,
                vy: (Math.random() - 0.5) * 0.25,
                r: Math.random() * 1.8 + 0.4,
                pulse: Math.random() * Math.PI * 2
            });
        }

        var theme = document.documentElement.getAttribute('data-theme') || 'dark';

        function getColors() {
            var t = document.documentElement.getAttribute('data-theme') || 'dark';
            if (t === 'light') {
                return {
                    particle: 'rgba(37,99,235,0.3)',
                    line: function (opacity) { return 'rgba(37,99,235,' + opacity + ')'; }
                };
            }
            return {
                particle: 'rgba(110,193,255,0.35)',
                line: function (opacity) { return 'rgba(110,193,255,' + opacity + ')'; }
            };
        }

        function draw() {
            ctx.clearRect(0, 0, c.width, c.height);
            var colors = getColors();

            for (var i = 0; i < N; i++) {
                var p = particles[i];
                p.x += p.vx;
                p.y += p.vy;
                p.pulse += 0.015;
                if (p.x < 0 || p.x > c.width) p.vx *= -1;
                if (p.y < 0 || p.y > c.height) p.vy *= -1;

                var pulseR = p.r + Math.sin(p.pulse) * 0.4;
                ctx.beginPath();
                ctx.arc(p.x, p.y, pulseR, 0, Math.PI * 2);
                ctx.fillStyle = colors.particle;
                ctx.fill();

                for (var j = i + 1; j < N; j++) {
                    var q = particles[j];
                    var dx = p.x - q.x, dy = p.y - q.y;
                    var dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 140) {
                        ctx.beginPath();
                        ctx.moveTo(p.x, p.y);
                        ctx.lineTo(q.x, q.y);
                        ctx.strokeStyle = colors.line((0.06 * (1 - dist / 140)).toFixed(4));
                        ctx.lineWidth = 0.5;
                        ctx.stroke();
                    }
                }
            }
            viParticles._anim = requestAnimationFrame(draw);
        }
        draw();
    },

    destroy: function () {
        if (this._anim) {
            cancelAnimationFrame(this._anim);
            this._anim = null;
        }
    }
};
