import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import client from '../api/client';
import StatusBadge from '../components/StatusBadge';
import RepoCard from '../components/RepoCard';
import DatabaseCard from '../components/DatabaseCard';
import TerminalView from '../components/TerminalView';
import AppViewer from '../components/AppViewer';
import WebTerminal from '../components/WebTerminal';
import UsageGuide from '../components/UsageGuide';
import DeleteConfirmModal from '../components/DeleteConfirmModal';

export default function JobDetail() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState('logs'); // 'logs' | 'app' | 'terminal'
  const [stopping, setStopping] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  const fetchJob = useCallback(async () => {
    try {
      const res = await client.get(`/jobs/${jobId}`);
      setJob(res.data);
    } catch (err) {
      setError(err.message);
    }
  }, [jobId]);

  useEffect(() => { fetchJob(); }, [fetchJob]);

  // Poll while in progress
  useEffect(() => {
    if (!job) return;
    const active = ['queued', 'cloning', 'analyzing', 'provisioning_db', 'installing'];
    if (!active.includes(job.status)) return;
    const interval = setInterval(fetchJob, 2000);
    return () => clearInterval(interval);
  }, [job?.status, fetchJob]);

  // Auto-switch to app/terminal tab when running
  useEffect(() => {
    if (!job) return;
    if (job.status === 'running') {
      if (job.app_type === 'web' && job.proxy_url) {
        setActiveTab('app');
      } else {
        setActiveTab('terminal');
      }
    }
  }, [job?.status]);

  // Elapsed timer
  useEffect(() => {
    if (!job?.started_at) return;
    const active = ['cloning', 'analyzing', 'provisioning_db', 'installing', 'running'];
    if (!active.includes(job.status)) {
      if (job.finished_at && job.started_at) {
        setElapsed(Math.floor((new Date(job.finished_at) - new Date(job.started_at)) / 1000));
      }
      return;
    }
    const tick = () => setElapsed(Math.floor((Date.now() - new Date(job.started_at).getTime()) / 1000));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [job?.started_at, job?.finished_at, job?.status]);

  const formatTime = (s) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  };

  const handleShare = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  // Poll until job status changes from current status
  const pollUntilChanged = useCallback(async (fromStatus) => {
    for (let i = 0; i < 15; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const res = await client.get(`/jobs/${jobId}`);
        setJob(res.data);
        if (res.data.status !== fromStatus) return;
      } catch { break; }
    }
  }, [jobId]);

  const handleStop = async () => {
    const prevStatus = job?.status;
    setStopping(true);
    setActiveTab('logs');
    try {
      await client.post(`/jobs/${jobId}/stop`);
      await pollUntilChanged(prevStatus);
    } catch (err) {
      setError(err.message);
    } finally {
      setStopping(false);
    }
  };

  const handleCancel = async () => {
    const prevStatus = job?.status;
    setStopping(true);
    setActiveTab('logs');
    try {
      await client.delete(`/jobs/${jobId}`);
      await pollUntilChanged(prevStatus);
    } catch (err) {
      setError(err.message);
    } finally {
      setStopping(false);
    }
  };

  const handleRestart = async () => {
    setStopping(true);
    setActiveTab('logs');
    try {
      await client.post(`/jobs/${jobId}/restart`);
      // Poll until it transitions through stopping → back to running/installing
      await pollUntilChanged('running');
    } catch (err) {
      setError(err.message);
    } finally {
      setStopping(false);
    }
  };

  const handleDelete = async () => {
    setStopping(true);
    setShowDeleteModal(false);
    try {
      await client.delete(`/jobs/${jobId}/purge`);
      navigate('/history');
    } catch (err) {
      setError(err.message);
      setStopping(false);
    }
  };

  if (error) {
    return (
      <div className="max-w-4xl mx-auto px-6 pt-12">
        <div className="bg-bg-surface border border-accent-red/30 rounded-lg p-6 text-center">
          <p className="text-accent-red">{error}</p>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="max-w-4xl mx-auto px-6 pt-12 text-center">
        <div className="text-text-muted animate-pulse">Loading job...</div>
      </div>
    );
  }

  const isRunning = job.status === 'running';
  const isActive = ['queued', 'cloning', 'analyzing', 'provisioning_db', 'installing', 'running'].includes(job.status);
  const isDone = ['success', 'failed', 'timeout'].includes(job.status);

  return (
    <div className="max-w-7xl mx-auto px-6 pt-6 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <StatusBadge status={job.status} />
          <h1 className="text-xl font-semibold text-text-primary">
            {job.repo_owner}/{job.repo_name}
          </h1>
          {elapsed > 0 && (
            <span className="text-text-muted text-sm font-[family-name:var(--font-mono)]">
              {formatTime(elapsed)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {isRunning && job.proxy_url && (
            <a
              href={job.proxy_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-1.5 text-xs bg-accent-green text-bg-primary font-semibold rounded hover:bg-accent-green/90 transition-colors"
            >
              Open App
            </a>
          )}
          {isRunning && (
            <button
              onClick={handleStop}
              disabled={stopping}
              className="px-3 py-1.5 text-xs border border-accent-red text-accent-red rounded hover:bg-accent-red/10 transition-colors disabled:opacity-50"
            >
              {stopping ? 'Stopping...' : 'Stop'}
            </button>
          )}
          {isDone && (
            <button
              onClick={handleRestart}
              disabled={stopping}
              className="px-3 py-1.5 text-xs bg-accent-green text-bg-primary font-semibold rounded hover:bg-accent-green/90 transition-colors disabled:opacity-50"
            >
              {stopping ? 'Starting...' : 'Restart'}
            </button>
          )}
          {isActive && !isRunning && (
            <button
              onClick={handleCancel}
              disabled={stopping}
              className="px-3 py-1.5 text-xs border border-accent-red text-accent-red rounded hover:bg-accent-red/10 transition-colors disabled:opacity-50"
            >
              {stopping ? 'Cancelling...' : 'Cancel'}
            </button>
          )}
          <button
            onClick={() => setShowDeleteModal(true)}
            disabled={stopping}
            className="px-3 py-1.5 text-xs border border-border text-text-muted rounded hover:border-accent-red hover:text-accent-red transition-colors disabled:opacity-50"
          >
            Delete
          </button>
          <button
            onClick={handleShare}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors px-3 py-1.5 border border-border rounded hover:border-text-muted"
          >
            {copied ? 'Copied!' : 'Share'}
          </button>
        </div>
      </div>

      {/* Running app banner */}
      {isRunning && job.proxy_url && (
        <div className="mb-4 px-4 py-3 bg-accent-green/10 border border-accent-green/30 rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-accent-green animate-pulse" />
            <span className="text-accent-green text-sm font-medium">App is live!</span>
            <a href={job.proxy_url} target="_blank" rel="noopener noreferrer"
              className="text-accent-green text-sm font-[family-name:var(--font-mono)] underline">
              {job.proxy_url}
            </a>
          </div>
          {job.expires_at && (
            <span className="text-text-muted text-xs">
              Expires: {new Date(job.expires_at).toLocaleTimeString()}
            </span>
          )}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 mb-4 border-b border-border">
        <button
          onClick={() => setActiveTab('logs')}
          className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
            activeTab === 'logs'
              ? 'text-text-primary border-accent-green'
              : 'text-text-muted border-transparent hover:text-text-secondary'
          }`}
        >
          Build Logs
        </button>
        {isRunning && job.app_type === 'web' && job.proxy_url && (
          <button
            onClick={() => setActiveTab('app')}
            className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
              activeTab === 'app'
                ? 'text-text-primary border-accent-green'
                : 'text-text-muted border-transparent hover:text-text-secondary'
            }`}
          >
            App Preview
          </button>
        )}
        {isRunning && (
          <button
            onClick={() => setActiveTab('terminal')}
            className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
              activeTab === 'terminal'
                ? 'text-text-primary border-accent-green'
                : 'text-text-muted border-transparent hover:text-text-secondary'
            }`}
          >
            Terminal
          </button>
        )}
      </div>

      {/* Content */}
      <div className="flex gap-6" style={{ minHeight: '70vh' }}>
        {/* Left panel - Info cards */}
        <div className="w-2/5 shrink-0 space-y-3">
          <RepoCard job={job} />

          {job.databases && job.databases.length > 0 && (
            <div>
              <p className="text-text-muted text-xs uppercase tracking-wider mb-2">Databases</p>
              {job.databases.map((db) => (
                <DatabaseCard key={db.id} database={db} />
              ))}
            </div>
          )}

          {isRunning && (
            <div className="bg-bg-surface border border-accent-green/30 rounded-lg p-4">
              <p className="text-text-muted text-xs uppercase tracking-wider mb-2">Running App</p>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <span className="text-text-muted">Type:</span>
                  <span className="text-text-primary">{job.app_type === 'web' ? 'Web App' : 'CLI'}</span>
                </div>
                {job.app_port && (
                  <div className="flex justify-between">
                    <span className="text-text-muted">Port:</span>
                    <span className="text-text-primary font-[family-name:var(--font-mono)]">{job.app_port}</span>
                  </div>
                )}
                {job.start_command && (
                  <div className="flex justify-between">
                    <span className="text-text-muted">Command:</span>
                    <span className="text-text-secondary text-xs font-[family-name:var(--font-mono)]">{job.start_command}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Usage guide — shown when running as CLI or when app is up */}
          {isRunning && (
            <UsageGuide job={job} />
          )}

          {job.error_message && (
            <div className="bg-bg-surface border border-accent-red/30 rounded-lg p-4">
              <p className="text-text-muted text-xs uppercase tracking-wider mb-2">Error</p>
              <p className="text-accent-red text-sm font-[family-name:var(--font-mono)]">{job.error_message}</p>
            </div>
          )}
        </div>

        {/* Right panel */}
        <div className="flex-1">
          {activeTab === 'logs' && (
            <TerminalView jobId={jobId} onComplete={fetchJob} />
          )}
          {activeTab === 'app' && isRunning && job.proxy_url && (
            <AppViewer proxyUrl={job.proxy_url} appPort={job.app_port} jobId={jobId} />
          )}
          {activeTab === 'terminal' && isRunning && (
            <WebTerminal jobId={jobId} />
          )}
        </div>
      </div>

      {/* Delete confirmation modal */}
      {showDeleteModal && (
        <DeleteConfirmModal
          job={job}
          onConfirm={handleDelete}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  );
}
