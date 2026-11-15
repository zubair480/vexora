// A visitor can arrive here with ?band=42 in the address. Send the visitor on to the dashboard and keep
// the band number. The file is named in full: opened by double-click (file://) a bare
// 'dashboard/' shows a list of files, while the page itself says how to open it properly.
location.replace('dashboard/index.html' + location.search);
