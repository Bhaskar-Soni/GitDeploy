import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import client from '../api/client';
import StatusBadge from '../components/StatusBadge';

export default function History() {
  const [jobs, setJobs] = useState([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    client.get(`/jobs?page=${page}&per_page=20`)
      .then((res) => {
        setJobs(res.data.jobs || []);
        setTotalPages(res.data.total_pages || 1);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page]);

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="max-w-4xl mx-auto px-6 pt-8 pb-12">
      <h1 className="text-2xl font-bold text-text-primary mb-6">Deploy History</h1>

      {loading ? (
        <div className="text-text-muted animate-pulse text-center py-12">Loading...</div>
      ) : jobs.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-text-muted">No jobs yet.</p>
          <Link to="/" className="text-accent-green text-sm hover:underline mt-2 inline-block">
            Deploy your first repo
          </Link>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {jobs.map((job) => (
              <Link
                key={job.id}
                to={`/jobs/${job.id}`}
                className="flex items-center justify-between px-4 py-3 bg-bg-surface border border-border rounded-lg hover:bg-bg-elevated transition-colors group"
              >
                <div className="flex items-center gap-4">
                  <StatusBadge status={job.status} />
                  <div>
                    <span className="text-text-primary text-sm group-hover:text-accent-green transition-colors">
                      {job.repo_owner}/{job.repo_name}
                    </span>
                    {job.detected_stack && (
                      <span className="text-text-muted text-xs ml-3">{job.detected_stack}</span>
                    )}
                  </div>
                </div>
                <span className="text-text-muted text-xs">{formatDate(job.created_at)}</span>
              </Link>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-4 mt-8">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 text-sm text-text-secondary border border-border rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Previous
              </button>
              <span className="text-text-muted text-sm">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1.5 text-sm text-text-secondary border border-border rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
