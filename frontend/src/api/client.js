/**
 * API client – thin wrapper around fetch() that talks to the Flask backend.
 * All methods return the parsed JSON body or throw an Error with the
 * server's error message.
 */

const BASE = ''   // empty = same origin (served by Flask)

async function request(method, path, body, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...opts.headers }
  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body != null ? JSON.stringify(body) : undefined,
    signal: opts.signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

const api = {
  // ── Documents ────────────────────────────────────────────────────────────
  getDocuments: (params = {}) =>
    request('GET', '/api/documents?' + new URLSearchParams(params)),

  getDocument: (id) =>
    request('GET', `/api/documents/${id}`),

  updateDocument: (id, data) =>
    request('PATCH', `/api/documents/${id}`, data),

  deleteDocument: (id) =>
    request('DELETE', `/api/documents/${id}`),

  getDocumentImageUrl: (id) =>
    `/api/documents/${id}/image`,

  rotateDocument: (id, direction) =>
    request('POST', `/api/documents/${id}/rotate`, { direction }),

  createLink: (docId, targetId, type, notes) =>
    request('POST', `/api/documents/${docId}/links`, {
      target_id: targetId, relationship_type: type, notes,
    }),

  deleteLink: (docId, linkId) =>
    request('DELETE', `/api/documents/${docId}/links/${linkId}`),

  addDocumentEntity: (docId, name, type, role) =>
    request('POST', `/api/documents/${docId}/entities`, { name, type, role }),

  removeDocumentEntity: (docId, entityId) =>
    request('DELETE', `/api/documents/${docId}/entities/${entityId}`),

  // ── Entities ─────────────────────────────────────────────────────────────
  getEntities: (params = {}) =>
    request('GET', '/api/entities?' + new URLSearchParams(params)),

  getEntity: (id) =>
    request('GET', `/api/entities/${id}`),

  updateEntity: (id, data) =>
    request('PATCH', `/api/entities/${id}`, data),

  deleteEntity: (id) =>
    request('DELETE', `/api/entities/${id}`),

  mergeEntities: (keepId, discardId) =>
    request('POST', '/api/entities/merge', { keep_id: keepId, discard_id: discardId }),

  // ── Search ───────────────────────────────────────────────────────────────
  search: (params = {}) =>
    request('GET', '/api/search?' + new URLSearchParams(params)),

  // ── Tags ─────────────────────────────────────────────────────────────────
  getTags: () =>
    request('GET', '/api/tags'),

  createTag: (name, color) =>
    request('POST', '/api/tags', { name, color }),

  updateTag: (id, data) =>
    request('PATCH', `/api/tags/${id}`, data),

  deleteTag: (id) =>
    request('DELETE', `/api/tags/${id}`),

  addDocTag: (docId, tagId) =>
    request('POST', `/api/documents/${docId}/tags`, { tag_id: tagId }),

  removeDocTag: (docId, tagId) =>
    request('DELETE', `/api/documents/${docId}/tags/${tagId}`),

  // ── Ingest ───────────────────────────────────────────────────────────────
  startIngest: (sourceArchive) =>
    request('POST', '/api/ingest', sourceArchive ? { source_archive: sourceArchive } : undefined),

  getArchives: () =>
    request('GET', '/api/archives'),

  getIngestStatus: () =>
    request('GET', '/api/ingest/status'),

  // ── Timeline ─────────────────────────────────────────────────────────────
  getTimeline: (params = {}) =>
    request('GET', '/api/timeline?' + new URLSearchParams(params)),

  // ── Network ──────────────────────────────────────────────────────────────
  getNetwork: (params = {}) =>
    request('GET', '/api/network?' + new URLSearchParams(params)),

  // ── Q&A ──────────────────────────────────────────────────────────────────
  askQuestion: (question) =>
    request('POST', '/api/qa', { question }),

  // ── Export ───────────────────────────────────────────────────────────────
  exportTimelineUrl: (params = {}) =>
    '/api/export/timeline?' + new URLSearchParams(params),

  exportEntityUrl: (entityId) =>
    `/api/export/entity/${entityId}`,

  exportSelection: (docIds) =>
    request('POST', '/api/export/selection', { doc_ids: docIds }),

  // ── Stats ─────────────────────────────────────────────────────────────────
  getStats: () =>
    request('GET', '/api/stats'),

  // ── Health ────────────────────────────────────────────────────────────────
  getHealth: () =>
    request('GET', '/api/health'),
}

export default api
