import { Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { ChatPage } from './pages/ChatPage'
import { PredictPage } from './pages/PredictPage'
import { IntegrationsPage } from './pages/IntegrationsPage'
import { SystemPage } from './pages/SystemPage'
import { HousePage } from './pages/HousePage'
import { SessionsPage } from './pages/SessionsPage'
import { HouseTransitionProvider } from './components/house/HouseTransition'
import { ProtectedRoute } from './components/ProtectedRoute'

/** Keep the shared chat socket and microphone alive between authenticated routes. */
function SignedInLayout() {
  const { pathname } = useLocation()
  return (
    <HouseTransitionProvider>
      <ChatPage active={pathname === '/chat'} />
      <Outlet />
    </HouseTransitionProvider>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute><SignedInLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<HousePage />} />
        <Route path="/chat" element={null} />
        <Route path="/predict" element={<PredictPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/system/:part?" element={<ProtectedRoute ownerOnly><SystemPage /></ProtectedRoute>} />
        <Route path="/learning" element={<Navigate to="/system" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
