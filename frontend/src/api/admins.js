/* ==========================================================================
   Farmers Forward - Admins API (owner only)
   The Admins page's calls, kept out of client.js so this feature adds a
   module instead of editing the shared one. They go through client.js's
   request(), so errors carry `.status` / `.payload` and a 401 sends the
   browser home exactly as every other call does.
   ========================================================================== */

import api from './client.js'

/**
 * List every admin, oldest first. Owner only: other admins get a 403.
 * @returns {Promise<{ admins: Array<{ id: string, email: string,
 *   display_name: string, role: string, is_active: boolean,
 *   created_at: string, last_login_at: string|null }> }>}
 */
export function listAdmins() {
  return api.request('/admins')
}

/**
 * Add an admin (owner only). Throws on 400 (bad email or display name) and
 * 409 (an admin with that email already exists).
 * @param {{ email: string, display_name: string }} admin
 * @returns {Promise<{ admin: Object }>} the new admin, shaped like a
 *   listAdmins() entry.
 */
export function createAdmin({ email, display_name }) {
  return api.request('/admins', {
    method: 'POST',
    body: { email, display_name },
  })
}

/**
 * Deactivate or reactivate an admin (owner only); nobody is ever deleted.
 * Throws on 400 (e.g. an owner deactivating themselves) and 404.
 * @param {string} id - admin id (a uuid).
 * @param {boolean} isActive
 * @returns {Promise<{ admin: Object }>} the updated admin.
 */
export function setAdminActive(id, isActive) {
  return api.request(`/admins/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: { is_active: isActive },
  })
}

const adminsApi = { listAdmins, createAdmin, setAdminActive }

export default adminsApi
