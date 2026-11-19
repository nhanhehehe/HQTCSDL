import { AuthProvider } from '../features/auth/AuthProvider'
import { AppRoutes } from './routes'

export function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}
