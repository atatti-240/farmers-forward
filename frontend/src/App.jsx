import { Routes, Route, Navigate } from 'react-router-dom'

import PublicHome from './pages/PublicHome.jsx'
import Login from './pages/Login.jsx'
import Otp from './pages/Otp.jsx'
import Dashboard from './pages/Dashboard.jsx'
import RequireAuth from './components/RequireAuth.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PublicHome />} />
      <Route path="/login" element={<Login />} />
      <Route path="/otp" element={<Otp />} />
      <Route
        path="/dashboard"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
