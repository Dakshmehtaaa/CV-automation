let token = '';

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.name = 'ApiError'; this.status = status; this.data = data; }
}

async function request(path, options = {}) {
  const { readTimeout = 30000, writeTimeout = 30000, ...fetchOptions } = options;
  const headers = { Accept: 'application/json', ...options.headers };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['X-Studio-Token'] = token;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), !options.method || options.method === 'GET' ? readTimeout : writeTimeout);
  let response;
  let data;
  try {
    response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...fetchOptions, headers, signal: controller.signal, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
    try { data = await response.json(); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError('The server returned an unreadable response.', response.status);
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === 'AbortError' ? 'The local server took too long to respond. Please try again.' : 'The local server is unavailable. Check that CV Studio is running, then retry.', 0);
  } finally { clearTimeout(timeout); }
  if (!response.ok) {
    const message = typeof data.error === 'string' ? data.error : data.error?.message || data.message || `Request failed (${response.status}).`;
    throw new ApiError(message, response.status, data);
  }
  return data;
}

export const items = (value) => Array.isArray(value) ? value : value?.items || [];
export const api = {
  async overview({ catalogs = false } = {}) { const data = await request('/api/overview', { readTimeout: 90000, ...(catalogs ? { headers: { 'X-Studio-Catalogs': '1' } } : {}) }); token = data.token || data.csrf_token || token; return data; },
  companyLogos: () => request('/api/company-logos'),
  roles: () => request('/api/roles', { readTimeout: 90000 }),
  role: id => request(`/api/roles/${encodeURIComponent(id)}`),
  runs: () => request('/api/runs', { readTimeout: 90000 }),
  audits: () => request('/api/audits', { readTimeout: 90000 }),
  run: id => request(`/api/runs/${encodeURIComponent(id)}`),
  documents: () => request('/api/documents'),
  document: id => request(`/api/documents/${encodeURIComponent(id)}`),
  saveDocument: (id, content, revision) => request(`/api/documents/${encodeURIComponent(id)}`, { method: 'PUT', body: { content, revision } }),
  settings: () => request('/api/settings'),
  saveSettings: settings => request('/api/settings', { method: 'PUT', body: settings }),
  saveRunSettings: settings => request('/api/settings/run', { method: 'PUT', body: settings }),
  resetRunSettings: () => request('/api/settings/run/reset', { method: 'POST', body: {} }),
  saveApplication: (id, status, revision) => request(`/api/applications/${encodeURIComponent(id)}`, { method: 'PUT', body: { status, revision } }),
  applyOptions: () => request('/api/apply-options'),
  motivation: (id, language) => request(`/api/applications/${encodeURIComponent(id)}/motivation?language=${encodeURIComponent(language)}`),
  prepareMotivation: (id, language) => request(`/api/applications/${encodeURIComponent(id)}/motivation`, { method: 'POST', body: { language } }),
  contacts: id => request(`/api/applications/${encodeURIComponent(id)}/contacts`),
  findContacts: (id, provider) => request(`/api/applications/${encodeURIComponent(id)}/contacts`, { method: 'POST', body: { provider } }),
  contactHandoff: (id) => request(`/api/applications/${encodeURIComponent(id)}/contact-handoff`, { method: 'POST', body: {} }),
  contactJob: id => request(`/api/contact-jobs/${encodeURIComponent(id)}`),
  contactsIndex: () => request('/api/contacts'),
  manualJobs: () => request('/api/manual-jobs'),
  addManualJob: payload => request('/api/manual-jobs', { method: 'POST', body: payload }),
  setManualContactChoice: (id, find_contacts) => request(`/api/manual-jobs/${encodeURIComponent(id)}/contact-choice`, {method: 'POST', body: {find_contacts}}),
  reviewManualJob: (id, decision, comment = '') => request(`/api/manual-jobs/${encodeURIComponent(id)}/review`, { method: 'POST', body: {decision, comment} }),
  applyFlow: () => request('/api/apply-flow'),
  saveApplyFlow: (id, body) => request(`/api/apply-flow/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  shortlist: () => request('/api/shortlist'),
  saveShortlist: (id, body) => request(`/api/shortlist/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  emailContacts: () => request('/api/email-contacts'),
  updateEmailContact: (key, fields) => request('/api/email-contacts', { method: 'POST', body: {key, fields} }),
  followupEmail: (key, role_id) => request('/api/followup-email', { method: 'POST', body: {key, role_id: role_id || ''}, writeTimeout: 300000 }),
  setEmailStatus: (key, status) => request('/api/email-contacts', { method: 'POST', body: {key, status} }),
  findEmail: (query, model) => request('/api/find-email', { method: 'POST', body: {query, model}, writeTimeout: 600000 }),
  draftEmail: (person, email, note, language, model, key, role_id) => request('/api/draft-email', { method: 'POST', body: {person, email, note, language, model, key: key || '', role_id: role_id || ''}, writeTimeout: 300000 }),
  helpChat: (messages, role_id) => request('/api/help-chat', { method: 'POST', body: {messages, role_id: role_id || ''}, writeTimeout: 600000 }),
  helpThreads: () => request('/api/help-threads'),
  saveHelpThread: (id, body) => request(`/api/help-threads/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  buildManualJob: id => request(`/api/manual-jobs/${encodeURIComponent(id)}/build`, { method: 'POST', body: {} }),
  jdInsights: () => request('/api/jd-insights'),
  stopExternalRun: (identity) => request('/api/pipeline/stop', { method: 'POST', body: identity }),
  jobs: () => request('/api/jobs'),
  checkConnections: () => request('/api/runtime/check', { method: 'POST', body: {} }),
  job: id => request(`/api/jobs/${encodeURIComponent(id)}`),
  startJob: kind => request('/api/jobs', { method: 'POST', body: { kind } }),
  stopJob: id => request(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST', body: {} }),
  jobInput: (id, payload) => request(`/api/jobs/${encodeURIComponent(id)}/input`, { method: 'POST', body: payload }),
};
