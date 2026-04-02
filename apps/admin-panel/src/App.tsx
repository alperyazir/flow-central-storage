import { useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import MainLayout from './layouts/MainLayout';
import DashboardPage from './pages/Dashboard';
import PublishersPage from './pages/Publishers';
import PublisherDetailPage from './pages/PublisherDetail';
import BooksPage from './pages/Books';
import AppsPage from './pages/Apps';
import BundlesPage from './pages/Bundles';
import TeachersPage from './pages/TeachersManagement';
import LoginPage from './pages/Login';
import SystemInfoPage from './pages/SystemInfo';
import ProcessingPage from './pages/Processing';
import ApiKeysPage from './pages/ApiKeys';
import TeacherDetailPage from './pages/TeacherDetail';
import ProtectedRoute from './routes/ProtectedRoute';
import { useAuthStore } from './stores/auth';
import { useThemeStore } from './stores/theme';

const App = () => {
  const hydrate = useAuthStore((state) => state.hydrate);
  const isHydrated = useAuthStore((state) => state.isHydrated);
  const isHydrating = useAuthStore((state) => state.isHydrating);
  const themeMode = useThemeStore((state) => state.mode);

  useEffect(() => {
    hydrate().catch((error) => {
      console.error('Failed to hydrate auth session', error);
    });
  }, [hydrate]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', themeMode);
  }, [themeMode]);

  if (!isHydrated || isHydrating) {
    return (
      <div className="flex min-h-screen items-center justify-center flex-col gap-2">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="text-muted-foreground">Preparing your session…</p>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="publishers" element={<PublishersPage />} />
          <Route path="publishers/:id" element={<PublisherDetailPage />} />
          <Route path="books" element={<BooksPage />} />
          <Route path="apps" element={<AppsPage />} />
          <Route path="bundles" element={<BundlesPage />} />
          <Route path="teachers" element={<TeachersPage />} />
          <Route path="teachers/:id" element={<TeacherDetailPage />} />
          <Route path="processing" element={<ProcessingPage />} />
          <Route path="api-keys" element={<ApiKeysPage />} />
          <Route path="system" element={<SystemInfoPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
};

export default App;
