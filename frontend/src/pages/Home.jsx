import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import client from '../api/client';
import StatusBadge from '../components/StatusBadge';

export default function Home() {
  const [repoUrl, setRepoUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [recentJobs, setRecentJobs] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    client.get('/jobs?per_page=5')
      .then((res) => setRecentJobs(res.data.jobs || []))
      .catch(() => {});
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!repoUrl.trim()) {
      setError('Please enter a GitHub repository URL');
      return;
    }

    setLoading(true);
    try {
      const res = await client.post('/jobs', { repo_url: repoUrl.trim() });
      navigate(`/jobs/${res.data.job_id}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const timeSince = (dateStr) => {
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  return (
    <div className="max-w-2xl mx-auto px-6 pt-24">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-text-primary mb-4">
          Deploy any GitHub repo in one click
        </h1>
        <p className="text-text-secondary text-lg">
          Paste a public GitHub URL. We'll clone it, analyze it, and install it automatically.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex gap-3 mb-4">
        <input
          type="text"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
          className="flex-1 px-4 py-3 bg-bg-surface border border-border rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-green/50 focus:ring-1 focus:ring-accent-green/30 font-[family-name:var(--font-mono)] text-sm transition-colors"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading}
          className="px-6 py-3 bg-accent-green text-bg-primary font-semibold rounded-lg hover:bg-accent-green/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
        >
          {loading ? 'Deploying...' : 'Deploy'}
        </button>
      </form>

      {error && (
        <p className="text-accent-red text-sm mb-6">{error}</p>
      )}

      {recentJobs.length > 0 && (
        <div className="mt-12">
          <h2 className="text-text-muted text-xs uppercase tracking-wider mb-4">Recent Deploys</h2>
          <div className="space-y-2">
            {recentJobs.map((job) => (
              <Link
                key={job.id}
                to={`/jobs/${job.id}`}
                className="flex items-center justify-between px-4 py-3 bg-bg-surface border border-border rounded-lg hover:border-border/80 hover:bg-bg-elevated transition-colors group"
              >
                <div className="flex items-center gap-3">
                  <StatusBadge status={job.status} />
                  <span className="text-text-primary text-sm group-hover:text-accent-green transition-colors">
                    {job.repo_owner}/{job.repo_name}
                  </span>
                  {job.detected_stack && (
                    <span className="text-text-muted text-xs">{job.detected_stack}</span>
                  )}
                </div>
                <span className="text-text-muted text-xs">{timeSince(job.created_at)}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
