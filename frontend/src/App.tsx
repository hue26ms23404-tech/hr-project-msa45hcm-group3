import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, Spin } from 'antd';
import viVN from 'antd/locale/vi_VN';
import { AuthProvider, useAuth } from './context/AuthContext';
import { MainLayout } from './components/MainLayout';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { AttendancePage } from './pages/AttendancePage';
import { LeavePage } from './pages/LeavePage';
import { PayrollPage } from './pages/PayrollPage';
import { KioskScanPage } from './pages/KioskScanPage';
import { EmployeesPage } from './pages/EmployeesPage';
import { lazy, Suspense } from 'react';

const KnowledgePage = lazy(() => import('./pages/KnowledgePage').then(module => ({ default: module.KnowledgePage })));
const KnowledgeViewerPage = lazy(() => import('./pages/KnowledgeViewerPage').then(module => ({ default: module.KnowledgeViewerPage })));
const AIDataAccessPage = lazy(() => import('./pages/AIDataAccessPage').then(module => ({ default: module.AIDataAccessPage })));

const ProtectedRoute: React.FC<{ children: React.ReactNode; requiredRoles?: string[] }> = ({
  children,
  requiredRoles,
}) => {
  const { user, loading, hasRole } = useAuth();

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (requiredRoles && !hasRole(requiredRoles)) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
};

function AppRoutes() {
  const { user } = useAuth();

  return (
    <Routes>
      <Route
        path="/login"
        element={user ? <Navigate to="/dashboard" replace /> : <LoginPage />}
      />

      <Route
        path="/"
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="attendance" element={<AttendancePage />} />
        <Route path="leaves" element={<LeavePage />} />
        <Route path="payroll" element={<PayrollPage />} />
        <Route path="kiosk" element={<KioskScanPage />} />
        <Route path="reports" element={<ProtectedRoute><Navigate to="/dashboard" state={{ openAI: true }} replace /></ProtectedRoute>} />
        <Route path="knowledge/view/:documentId" element={<Suspense fallback={<Spin />}><KnowledgeViewerPage /></Suspense>} />
        <Route path="knowledge" element={<ProtectedRoute requiredRoles={['ADMIN', 'HR']}><Suspense fallback={<Spin />}><KnowledgePage /></Suspense></ProtectedRoute>} />
        <Route path="ai-access" element={<ProtectedRoute requiredRoles={['ADMIN']}><Suspense fallback={<Spin />}><AIDataAccessPage /></Suspense></ProtectedRoute>} />
        <Route
          path="employees"
          element={
            <ProtectedRoute requiredRoles={['ADMIN', 'HR', 'MANAGER']}>
              <EmployeesPage />
            </ProtectedRoute>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ConfigProvider
      locale={viVN}
      theme={{
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 8,
          fontFamily: "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          colorBgContainer: '#ffffff',
        },
      }}
    >
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </ConfigProvider>
  );
}
