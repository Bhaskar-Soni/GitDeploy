import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setAuth } from '../api/auth';

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ username, password }),
      });

      if (!resp.ok) {
        let detail = 'Login failed';
        try {
          const errData = await resp.json();
          detail = errData.detail || detail;
        } catch {
          detail = `Server error (${resp.status})`;
        }
        throw new Error(detail);
      }

      const data = await resp.json();
      setAuth(data.access_token, data.username);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-accent-green font-[family-name:var(--font-mono)] tracking-tight">
            GitDeploy
          </h1>
          <p className="text-text-muted text-sm mt-2">Sign in to continue</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="bg-bg-surface border border-border rounded-xl p-6 space-y-4">
          {error && (
            <div className="px-3 py-2 bg-accent-red/10 border border-accent-red/30 rounded-lg">
              <p className="text-accent-red text-sm">{error}</p>
            </div>
          )}

          <div>
            <label className="block text-text-muted text-xs uppercase tracking-wider mb-1.5">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2.5 bg-bg-primary border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:border-accent-green/50 focus:ring-1 focus:ring-accent-green/30"
              placeholder="admin"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="block text-text-muted text-xs uppercase tracking-wider mb-1.5">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2.5 bg-bg-primary border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:border-accent-green/50 focus:ring-1 focus:ring-accent-green/30"
              placeholder="Enter password"
              required
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-accent-green text-bg-primary font-semibold text-sm rounded-lg hover:bg-accent-green/90 transition-colors disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

      </div>
    </div>
  );
}
