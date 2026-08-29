import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/AppLayout';
import { ProvenanceProvider } from './components/provenance/ProvenanceContext';
import DataSourcesScreen from './screens/DataSourcesScreen';
import OntologyScreen from './screens/OntologyScreen';
import ViewerScreen from './screens/ViewerScreen';
import CurationScreen from './screens/CurationScreen';
import ExportScreen from './screens/ExportScreen';
import ObservabilityScreen from './screens/ObservabilityScreen';
import SettingsScreen from './screens/SettingsScreen';
import ChatScreen from './screens/ChatScreen';
import ScopesScreen from './screens/ScopesScreen';
import LoginScreen from './screens/LoginScreen';
import ForgotPasswordScreen from './screens/ForgotPasswordScreen';
import ResetPasswordScreen from './screens/ResetPasswordScreen';
import AcceptInvitationScreen from './screens/AcceptInvitationScreen';
import ProfileScreen from './screens/ProfileScreen';
import MailScreen from './screens/MailScreen';
import MailOAuthCallbackScreen from './screens/MailOAuthCallbackScreen';
import { Outlet } from 'react-router-dom';

const queryClient = new QueryClient();

function ProtectedRoute() {
  const token = localStorage.getItem('synapse_access_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ProvenanceProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginScreen />} />
            <Route path="/forgot-password" element={<ForgotPasswordScreen />} />
            <Route path="/reset-password" element={<ResetPasswordScreen />} />
            <Route path="/accept-invitation" element={<AcceptInvitationScreen />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/" element={<AppLayout />}>
                <Route index element={<Navigate to="/sources" replace />} />
                <Route path="sources" element={<DataSourcesScreen />} />
                <Route path="ontology" element={<OntologyScreen />} />
                <Route path="viewer" element={<ViewerScreen />} />
                <Route path="chat" element={<ChatScreen />} />
                <Route path="curation" element={<CurationScreen />} />
                <Route path="export" element={<ExportScreen />} />
                <Route path="observability" element={<ObservabilityScreen />} />
                <Route path="scopes" element={<ScopesScreen />} />
                <Route path="settings" element={<SettingsScreen />} />
                <Route path="profile" element={<ProfileScreen />} />
                <Route path="mail" element={<MailScreen />} />
                <Route path="mail/oauth/callback" element={<MailOAuthCallbackScreen />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </ProvenanceProvider>
    </QueryClientProvider>
  );
}

export default App;
