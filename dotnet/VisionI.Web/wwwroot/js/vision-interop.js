// Vision-I — General interop utilities
window.visionI = {
    printPage: function () {
        window.print();
    },

    scrollToBottom: function (elementId) {
        var el = document.getElementById(elementId);
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    },

    scrollIntoView: function (elementId) {
        var el = document.getElementById(elementId);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    },

    copyToClipboard: function (text) {
        if (navigator.clipboard) {
            return navigator.clipboard.writeText(text);
        }
        var ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
    }
};
