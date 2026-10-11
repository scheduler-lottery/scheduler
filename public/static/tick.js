// A steady beat for app.js's wait for Scheduler Helper. A worker keeps its
// pace while the page is hidden; the page's own timers slow to once a minute.
setInterval(function () { postMessage(0); }, 1500);
