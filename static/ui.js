export const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
export const text = (value, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value);
export const formatNumber = value => typeof value === 'number' ? new Intl.NumberFormat('en-GB').format(value) : text(value);
export const date = (value, options = {}) => {
  if (!value) return 'Not recorded';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Europe/Paris', ...options }).format(parsed);
};
export const relativeDate = value => {
  if (!value) return 'Date not recorded';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  const diff = (Date.now() - parsed.getTime()) / 1000;
  if (diff < 0) return date(value);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)} days ago`;
  return date(value, { year: undefined });
};
export const initials = value => String(value || 'CV').split(/[\s–—-]+/).filter(Boolean).slice(0, 2).map(word => word[0]).join('').toUpperCase();
export const statusLabel = value => String(value || 'Not recorded').replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
export const tone = value => {
  const status = String(value || '').toLowerCase();
  if (/fail|error|reject|expired|invalid|cancel|stopp|interrupted/.test(status)) return 'danger';
  if (/block|hold|held|wait|pending|partial|incomplete|attention|unverified|unavailable/.test(status)) return 'warning';
  if (/active|running|in.progress|building|started|queued/.test(status)) return 'live';
  if (/complete|done|pass|ready|approved|success|applied|available/.test(status)) return 'success';
  return 'neutral';
};
export const badge = (value, label) => `<span class="badge badge-${tone(value)}"><span class="badge-dot" aria-hidden="true"></span>${escape(label || statusLabel(value))}</span>`;
export const safeUrl = value => {
  if (typeof value !== 'string') return '';
  if (value.startsWith('/') && !value.startsWith('//') && !value.includes('\\')) return value;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
};
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/>',
  pipeline: '<circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="M5 8.5V14a4 4 0 0 0 4 4h7.5M7.5 6H15a4 4 0 0 1 4 4v1.5M16 10l3 3 3-3"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  book: '<path d="M12 5v15M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z"/>',
  settings: '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"/><path d="m9 3-.7 2.3-2 .9L4 5.7 2.5 8.3 4.2 10v2.3L2.5 14l1.5 2.6 2.3-.5 2 .9.7 2.3h3l.7-2.3 2-.9 2.3.5 1.5-2.6-1.7-1.7V10l1.7-1.7L17 5.7l-2.3.5-2-.9L12 3Z" transform="translate(1.3 1)"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5"/>',
  upRight: '<path d="M6 18 18 6M6 6h12v12"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.4 7a8 8 0 0 1 13.2-1L20 8M4 16l1.4 2a8 8 0 0 0 13.2-1"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  dot: '<circle cx="12" cy="12" r="3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.1"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5M12 17v.1"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  sun: '<g stroke-width="2"><circle cx="12" cy="12" r="3.75"/><path d="M12 3V5.25"/><path d="M18.364 5.63604L16.773 7.22703"/><path d="M21 12H18.75"/><path d="M18.364 18.364L16.773 16.773"/><path d="M12 18.75V21"/><path d="M7.22703 16.773L5.63604 18.364"/><path d="M5.25 12H3"/><path d="M7.22703 7.22703L5.63604 5.63604"/></g>',
  moon: '<path d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z"/>',
  monitor: '<rect x="3" y="3" width="18" height="13" rx="2"/><path d="M8 21h8M12 16v5"/>',
  edit: '<path d="m16 3 5 5L8 21H3v-5zM13 6l5 5"/>',
  save: '<path d="M5 3h12l4 4v14H3V3zM7 3v6h10V3M7 21v-8h10v8"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  leaf: '<path d="M19 3c-3 0-13 1-15 8-2 8 8 12 12 5 3-5 3-9 3-13ZM4 21 15 9"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  terminal: '<path d="m5 7 5 5-5 5M13 17h6"/>',
  linkedin: '<rect x="3" y="3" width="18" height="18" rx="3.5"/><path d="M8 10.5V17M8 7.2v.01M12 17v-6.5M12 13.6a2.9 2.9 0 0 1 5.8 0V17"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  filter: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  link: '<path d="m9 15 6-6M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(0 -1)"/>',
};
export const icon = (name, className = '') => `<svg class="icon ${escape(className)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
export const empty = (title, description, symbol = 'folder', action = '') => `<div class="empty-state"><span class="empty-icon">${icon(symbol)}</span><h3>${escape(title)}</h3><p>${escape(description)}</p>${action}</div>`;
export const loading = label => `<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><span>${escape(label || 'Reading your workspace…')}</span></div>`;
export const errorState = (message, retry = 'refresh') => `<div class="error-state" role="alert">${icon('alert')}<div><h3>Something needs attention</h3><p>${escape(message)}</p><button class="button button-secondary button-small" data-action="${escape(retry)}">${icon('refresh')}Try again</button></div></div>`;

function inlineMarkdown(value) {
  return escape(value).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>').replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
    const href = safeUrl(url.replace(/&amp;/g, '&'));
    return href ? `<a href="${escape(href)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
  });
}
export function markdown(content) {
  const lines = String(content || '').split('\n');
  let result = '', list = '', code = false, buffer = [];
  const closeList = () => { if (list) { result += `</${list}>`; list = ''; } };
  for (const line of lines) {
    if (/^```/.test(line)) { closeList(); if (code) { result += `<pre><code>${escape(buffer.join('\n'))}</code></pre>`; buffer = []; } code = !code; continue; }
    if (code) { buffer.push(line); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    const bullet = line.match(/^\s*[-*+]\s+(.+)/);
    const numbered = line.match(/^\s*\d+\.\s+(.+)/);
    if (heading) { closeList(); const level = Math.min(heading[1].length + 1, 6); result += `<h${level}>${inlineMarkdown(heading[2])}</h${level}>`; }
    else if (bullet || numbered) { const type = bullet ? 'ul' : 'ol'; if (list !== type) { closeList(); result += `<${type}>`; list = type; } result += `<li>${inlineMarkdown((bullet || numbered)[1])}</li>`; }
    else { closeList(); if (/^\s*---+\s*$/.test(line)) result += '<hr>'; else if (/^>\s?/.test(line)) result += `<blockquote>${inlineMarkdown(line.replace(/^>\s?/, ''))}</blockquote>`; else if (line.trim()) result += `<p>${inlineMarkdown(line)}</p>`; }
  }
  closeList();
  if (code) result += `<pre><code>${escape(buffer.join('\n'))}</code></pre>`;
  return result || '<p class="muted">This document is empty.</p>';
}
