'use strict';
// The widget hands this page its key and its project in the URL fragment.
// A fragment is never sent to a server, so the key does not appear in the
// request line; the page keeps it in memory and sends it as a header.
window.smithSession = (() => {
  const params = new URLSearchParams(location.hash.slice(1));
  const slot = 'smith-dashboard-key:' + location.port;
  let key = params.get('k') || '';
  const project = params.get('project') || '';
  const view = params.get('view') || '';
  try {
    // Kept for this tab only, so a reload keeps working while a new tab from
    // somewhere else still has to be opened by the widget.
    if (key) sessionStorage.setItem(slot, key);
    else key = sessionStorage.getItem(slot) || '';
  } catch { /* private windows and blocked storage: the key stays in memory */ }
  if (params.has('k') || params.has('project') || params.has('view')) {
    // Take the key back out of the address bar before anything can copy it.
    history.replaceState(null, '', location.pathname + (view ? '#' + view : ''));
  }
  async function api(path) {
    const response = await fetch(path, {cache: 'no-store', headers: {'X-Smith-Key': key}});
    if (response.status === 403) throw new Error('Reopen the dashboard from the widget.');
    if (!response.ok) throw new Error('Request failed');
    return response.json();
  }
  return {api, project, view, get key() {return key;}};
})();
