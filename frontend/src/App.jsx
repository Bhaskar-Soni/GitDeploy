import { Routes, Route, Link, Navigate, useNavigate } from 'react-router-dom';
import Home from './pages/Home';
import JobDetail from './pages/JobDetail';
import History from './pages/History';
import Settings from './pages/Settings';
import Login from './pages/Login';
import { isAuthenticated, clearAuth, getUsername } from './api/auth';

function ProtectedRoute({ children }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function NavBar() {
  const navigate = useNavigate();

  const handleLogout = () => {
    clearAuth();
    navigate('/login', { replace: true });
  };

  return (
    <nav className="border-b border-border bg-bg-surface px-6 py-3 flex items-center justify-between">
      <Link to="/" className="text-xl font-bold text-accent-green font-[family-name:var(--font-mono)] tracking-tight hover:opacity-80 transition-opacity">
        GitDeploy
      </Link>
      <div className="flex items-center gap-6 text-sm">
        <Link to="/" className="text-text-secondary hover:text-text-primary transition-colors">
          Home
        </Link>
        <Link to="/history" className="text-text-secondary hover:text-text-primary transition-colors">
          History
        </Link>
        <Link to="/settings" className="text-text-secondary hover:text-text-primary transition-colors">
          Settings
        </Link>
        <div className="flex items-center gap-3 ml-2 pl-4 border-l border-border">
          <span className="text-text-muted text-xs">{getUsername()}</span>
          <button
            onClick={handleLogout}
            className="text-text-muted hover:text-accent-red transition-colors text-xs"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-bg-primary">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={
          <ProtectedRoute>
            <NavBar />
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/jobs/:jobId" element={<JobDetail />} />
              <Route path="/history" element={<History />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </ProtectedRoute>
        } />
      </Routes>
    </div>
  );
}
