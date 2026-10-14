import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './hooks/useAuth.tsx'
import { DesktopSessionProvider } from './hooks/useDesktopSession.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <DesktopSessionProvider>
          <App />
        </DesktopSessionProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
