import { useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import MainLayout from './layouts/MainLayout';
import DashboardPage from './pages/Dashboard';
import PublishersPage from './pages/Publishers';
import PublisherDetailPage from './pages/PublisherDetail';
import BooksPage from './pages/Books';
import BookDetailPage from './pages/BookDetail';
import AppsPage from './pages/Apps';
import BundlesPage from './pages/Bundles';
import TeachersPage from './pages/TeachersManagement';
import LoginPage from './pages/Login';
import SystemInfoPage from './pages/SystemInfo';
import ProcessingPage from './pages/Processing';
import AIDataPage from './pages/AIData';
import ApiKeysPage from './pages/ApiKeys';
import CalculatePage from './pages/Calculate';
import TeacherDetailPage from './pages/TeacherDetail';
import SettingsPage from './pages/Settings';
import ProtectedRoute from './routes/ProtectedRoute';
import { useAuthStore } from './stores/auth';
import { useThemeStore } from './stores/theme';
import { useSettingsStore } from './stores/settings';

const App = () => {
  const hydrate = useAuthStore((state) => state.hydrate);
  const isHydrated = useAuthStore((state) => state.isHydrated);
  const isHydrating = useAuthStore((state) => state.isHydrating);
  const token = useAuthStore((state) => state.token);
  const tokenType = useAuthStore((state) => state.tokenType);
  const themeMode = useThemeStore((state) => state.mode);
  const loadSettings = useSettingsStore((state) => state.load);

  useEffect(() => {
    hydrate().catch((error) => {
      console.error('Failed to hydrate auth session', error);
    });
  }, [hydrate]);

  // Once authenticated, preload app settings so upload dialogs can read
  // defaults (e.g. auto-bundle) without each fetching on open.
  useEffect(() => {
    if (token) {
      loadSettings(token, tokenType ?? 'Bearer');
    }
  }, [token, tokenType, loadSettings]);

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
          <Route path="books/:id" element={<BookDetailPage />} />
          <Route path="apps" element={<AppsPage />} />
          <Route path="bundles" element={<BundlesPage />} />
          <Route path="teachers" element={<TeachersPage />} />
          <Route path="teachers/:id" element={<TeacherDetailPage />} />
          <Route path="processing" element={<ProcessingPage />} />
          <Route path="processing/:bookId/ai-data" element={<AIDataPage />} />
          <Route path="api-keys" element={<ApiKeysPage />} />
          <Route path="calculate" element={<CalculatePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="system" element={<SystemInfoPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
};

export default App;
