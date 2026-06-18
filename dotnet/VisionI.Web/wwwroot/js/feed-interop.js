// Infinite-scroll helper. Attaches IntersectionObserver to sentinel el,
// calls dotnetRef.invokeMethodAsync(method) when sentinel enters viewport.
window.viInfiniteScroll = function (sentinel, dotnetRef, method) {
    if (!sentinel || !dotnetRef) return;
    // Clean up any prior observer on this element
    if (sentinel.__viObs) { try { sentinel.__viObs.disconnect(); } catch (e) { } }
    var obs = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
            if (e.isIntersecting) {
                try { dotnetRef.invokeMethodAsync(method); } catch (err) { console.warn('viInfiniteScroll', err); }
            }
        });
    }, { root: null, rootMargin: '400px', threshold: 0 });
    obs.observe(sentinel);
    sentinel.__viObs = obs;
};
