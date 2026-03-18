import React from 'react'
import { Routes, Route } from 'react-router-dom'
import { QueryProvider } from './providers'
import { ToastProvider } from './components'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import ResourceManagement from './pages/ResourceManagement'
import ResourceDetail from './pages/ResourceDetail'
import SessionManagement from './pages/SessionManagement'
import FileExplorer from './pages/FileExplorer'
import SemanticSearch from './pages/SemanticSearch'
import Layout from './components/common/Layout'


const AppContent: React.FC = () => {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-600">Loading authentication...</div>
      </div>
    )
  }

  // If not authenticated, show login page without layout
  if (!isAuthenticated) {
    return <Login />
  }

  // Authenticated - show layout with protected routes
  return (
    <Layout>
      <React.Suspense fallback={<div>Loading...</div>}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/resources" element={<ResourceManagement />} />
          <Route path="/resources/:uri" element={<ResourceDetail />} />
          <Route path="/sessions" element={<SessionManagement />} />
          <Route path="/filesystem" element={<FileExplorer />} />
          <Route path="/search" element={<SemanticSearch />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </React.Suspense>
    </Layout>
  )
}

const App: React.FC = () => {
  return (
    <QueryProvider>
      <ToastProvider>
        <AuthProvider>
          <AppContent />
        </AuthProvider>
      </ToastProvider>
    </QueryProvider>
  )
}

export default App
