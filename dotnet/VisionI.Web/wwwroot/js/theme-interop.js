// Vision-I — Theme management interop
window.viTheme = {
    get: function () {
        return localStorage.getItem('vi-theme') || 'dark';
    },

    set: function (theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('vi-theme', theme);
    },

    init: function () {
        var saved = localStorage.getItem('vi-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', saved);
        return saved;
    },

    toggle: function () {
        var current = document.documentElement.getAttribute('data-theme') || 'dark';
        var next = current === 'dark' ? 'light' : 'dark';
        this.set(next);
        return next;
    }
};

// Auto-init on page load
(function () {
    var saved = localStorage.getItem('vi-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
})();
