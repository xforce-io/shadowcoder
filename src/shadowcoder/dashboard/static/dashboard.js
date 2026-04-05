(function () {
    "use strict";

    var logContainer = document.getElementById("log-entries");
    var autoScroll = true;

    if (logContainer) {
        logContainer.addEventListener("scroll", function () {
            var atBottom =
                logContainer.scrollHeight - logContainer.scrollTop - logContainer.clientHeight < 30;
            autoScroll = atBottom;
        });
        var observer = new MutationObserver(function () {
            if (autoScroll) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        });
        observer.observe(logContainer, { childList: true, subtree: true });
    }

    var elapsedEl = document.getElementById("elapsed-time");
    if (elapsedEl) {
        var created = elapsedEl.dataset.created;
        if (created) {
            var startTime = new Date(created).getTime();
            function updateElapsed() {
                var now = Date.now();
                var diff = Math.floor((now - startTime) / 1000);
                var m = Math.floor(diff / 60);
                var s = diff % 60;
                elapsedEl.textContent = m + "m " + s + "s";
            }
            updateElapsed();
            setInterval(updateElapsed, 1000);
        }
    }

    document.body.addEventListener("htmx:sseError", function () {
        console.log("SSE connection lost, reconnecting in 3s...");
        setTimeout(function () {
            htmx.trigger(document.body, "htmx:load");
        }, 3000);
    });
})();
