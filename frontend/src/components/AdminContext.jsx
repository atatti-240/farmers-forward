import { createContext, useContext } from 'react'

/**
 * The signed-in admin's profile, provided by `<RequireAuth>` once `api.me()`
 * has confirmed the session.
 */
export const AdminContext = createContext(null)

/**
 * The signed-in admin, or null outside a `<RequireAuth>` route.
 * @returns {{ id: string, email: string, display_name: string,
 *   role: 'owner'|'admin' } | null}
 */
export function useAdmin() {
  return useContext(AdminContext)
}
