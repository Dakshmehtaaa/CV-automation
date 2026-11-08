import { icon } from './ui.js';

const sessions = new WeakMap();

export function mountProspect(main) {
  let page = main.querySelector('.page-prospect');
  if (page) {
    for (const child of [...main.childNodes]) if (child !== page) child.remove();
    page.hidden = false;
    return page;
  }
  main.innerHTML = `<div class="page page-prospect is-entering">
    <div class="page-heading"><div><h1>Prospect</h1><p class="prospect-subtitle">Your Prospector workspace, inside CV Studio.</p></div>
      <div class="heading-actions"><a class="button button-secondary" href="/prospect/" target="_blank" rel="noopener">${icon('upRight')}Open full view</a></div></div>
    <section class="panel prospect-panel" aria-label="Prospector workspace">
      <div class="prospect-status" role="status" aria-live="polite"><span class="prospect-status-icon">${icon('search')}</span><h2>Opening Prospector…</h2><p>Starting the local workspace. This may take a moment.</p><button type="button" class="button button-secondary" data-prospect-retry hidden>${icon('refresh')}Try again</button></div>
      <iframe class="prospect-frame" title="Prospector workspace" hidden></iframe>
    </section></div>`;
  page = main.querySelector('.page-prospect');
  sessions.set(page, { pending: null, ready: false, loadTimer: null });
  page.querySelector('[data-prospect-retry]').addEventListener('click', () => { void checkProspect(page); });
  void checkProspect(page);
  return page;
}

export function replaceStudioContent(main, content) {
  // Leave the iframe connected when users visit another Studio page, so a
  // navigation round-trip also preserves filters, drafts and scroll position.
  const prospect = main.querySelector('.page-prospect');
  for (const child of [...main.childNodes]) if (child !== prospect) child.remove();
  if (prospect) prospect.hidden = true;
  main.insertAdjacentHTML('beforeend', content);
}

export async function refreshProspect(main) {
  return checkProspect(mountProspect(main));
}

function setStatus(page, title, message, failed = false) {
  const status = page.querySelector('.prospect-status');
  status.hidden = false;
  status.classList.toggle('is-error', failed);
  status.querySelector('h2').textContent = title;
  status.querySelector('p').textContent = message;
  status.querySelector('[data-prospect-retry]').hidden = !failed;
}

async function checkProspect(page) {
  const session = sessions.get(page);
  if (session.pending) return session.pending;
  // Checking availability never reloads a working frame or starts research.
  if (!session.ready) setStatus(page, 'Opening Prospector…', 'Starting the local workspace. This may take a moment.');
  session.pending = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch('/prospect/api/state', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`The local workspace returned ${response.status}.`);
      const data = await response.json();
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('The local workspace returned an unreadable response.');
      if (!page.isConnected || session.ready) return true;
      const frame = page.querySelector('.prospect-frame');
      setStatus(page, 'Loading Prospector…', 'The workspace is ready. Loading its tools.');
      frame.onload = () => {
        clearTimeout(session.loadTimer);
        if (!page.isConnected) return;
        // Browsers also fire load for HTTP error documents. Check the real app.
        if (!frame.contentDocument?.querySelector('#app')) {
          frame.hidden = true;
          setStatus(page, 'Prospector could not open', 'The workspace page did not load. Try again, or use Open full view.', true);
          return;
        }
        session.ready = true;
        frame.hidden = false;
        page.querySelector('.prospect-status').hidden = true;
      };
      frame.onerror = () => {
        clearTimeout(session.loadTimer);
        frame.hidden = true;
        setStatus(page, 'Prospector could not open', 'Check the local workspace, then try again. You can also use Open full view.', true);
      };
      session.loadTimer = setTimeout(() => {
        if (!session.ready && page.isConnected) setStatus(page, 'Prospector is taking longer than expected', 'Try again, or use Open full view to check the workspace.', true);
      }, 30000);
      frame.src = '/prospect/';
      return true;
    } catch (error) {
      if (page.isConnected && !session.ready) setStatus(page, 'Prospector is unavailable', error.name === 'AbortError' ? 'The local workspace took too long to respond. Try again, or use Open full view.' : `${error.message} Try again, or use Open full view.`, true);
      return false;
    } finally {
      clearTimeout(timeout);
      session.pending = null;
    }
  })();
  return session.pending;
}
