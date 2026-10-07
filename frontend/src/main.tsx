import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './hooks/useAuth.tsx'
import { VoiceAgentProvider } from './hooks/useVoiceAgent.tsx'
import { DesktopSessionProvider } from './hooks/useDesktopSession.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <VoiceAgentProvider>
          <DesktopSessionProvider>
            <App />
          </DesktopSessionProvider>
        </VoiceAgentProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
