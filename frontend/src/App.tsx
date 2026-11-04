import { Routes, Route, Navigate } from 'react-router-dom'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/LoginPage'
import { ChatPage } from './pages/ChatPage'
import { PredictPage } from './pages/PredictPage'
import { IntegrationsPage } from './pages/IntegrationsPage'
import { SystemPage } from './pages/SystemPage'
import { ProtectedRoute } from './components/ProtectedRoute'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <Navigate to="/chat" replace />
          </ProtectedRoute>
        }
      />
      <Route
        path="/chat"
        element={
          <ProtectedRoute>
            <ChatPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/predict"
        element={
          <ProtectedRoute>
            <PredictPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/integrations"
        element={
          <ProtectedRoute>
            <IntegrationsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/system/:part?"
        element={
          <ProtectedRoute ownerOnly>
            <SystemPage />
          </ProtectedRoute>
        }
      />
      {/* The Learning page became the System page's Learning part. */}
      <Route path="/learning" element={<Navigate to="/system" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
