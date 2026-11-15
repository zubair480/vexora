// For a page opened as a file (by double-click). The page's program (app.js) cannot load
// then, but this small plain script can. The help on the page prints the address to type;
// this adds what the page was opened with after the "?" (the band number and anything
// else, for example ?band=42), so the address leads to the same band.
if (location.protocol === 'file:') {
  document.getElementById('needs-server-address').textContent += location.search;
}
