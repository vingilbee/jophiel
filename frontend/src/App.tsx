import { Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { ChatPage } from './pages/ChatPage'
import { PredictPage } from './pages/PredictPage'
import { IntegrationsPage } from './pages/IntegrationsPage'
import { SystemPage } from './pages/SystemPage'
import { ProtectedRoute } from './components/ProtectedRoute'

/** Keep the shared chat socket and microphone alive between authenticated routes. */
function SignedInLayout() {
  const { pathname } = useLocation()
  return (
    <>
      <ChatPage active={pathname === '/chat'} />
      <Outlet />
    </>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute><SignedInLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={null} />
        <Route path="/predict" element={<PredictPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/system/:part?" element={<ProtectedRoute ownerOnly><SystemPage /></ProtectedRoute>} />
        <Route path="/learning" element={<Navigate to="/system" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
