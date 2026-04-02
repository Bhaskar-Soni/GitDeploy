// Default credentials for popular apps
const KNOWN_CREDS = {
  grafana:       { user: 'admin', password: 'admin', note: 'Change on first login' },
  gitea:         { note: 'Complete setup wizard on first visit' },
  portainer:     { note: 'Create admin account on first visit (5 min timeout)' },
  filebrowser:   { user: 'admin', password: 'admin' },
  nocodb:        { note: 'Create account on first visit' },
  appsmith:      { note: 'Create account on first visit' },
  mattermost:    { note: 'Create account on first visit' },
  netdata:       { note: 'No login required' },
  dozzle:        { note: 'No login required' },
  heimdall:      { note: 'No login required' },
  'uptime-kuma': { note: 'Create account on first visit' },
};

function getCreds(repoName) {
  if (!repoName) return null;
  const key = repoName.toLowerCase();
  return Object.entries(KNOWN_CREDS).find(([k]) => key.includes(k))?.[1] || null;
}

export default function UsageGuide({ job }) {
  const creds = getCreds(job.repo_name);
  const isCli = job.app_type === 'cli';
  const hasUsage = job.usage_instructions;

  if (!creds && !isCli && !hasUsage) return null;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 space-y-3">
      <p className="text-text-muted text-xs uppercase tracking-wider">How to Use</p>

      {/* Default credentials for known web apps */}
      {creds && (
        <div className="space-y-1.5">
          <p className="text-text-secondary text-xs font-semibold">Default Credentials</p>
          {creds.user && (
            <div className="flex justify-between text-sm">
              <span className="text-text-muted">Username</span>
              <code className="text-accent-green font-[family-name:var(--font-mono)]">{creds.user}</code>
            </div>
          )}
          {creds.password && (
            <div className="flex justify-between text-sm">
              <span className="text-text-muted">Password</span>
              <code className="text-accent-green font-[family-name:var(--font-mono)]">{creds.password}</code>
            </div>
          )}
          {creds.note && (
            <p className="text-text-muted text-xs italic">{creds.note}</p>
          )}
        </div>
      )}

      {/* AI-generated usage instructions */}
      {hasUsage && (
        <div className="space-y-1.5">
          {isCli && (
            <p className="text-text-secondary text-xs font-semibold">CLI Application — use the Terminal tab</p>
          )}
          <div className="text-xs text-text-secondary font-[family-name:var(--font-mono)] bg-bg-primary rounded px-2 py-1.5 whitespace-pre-wrap break-all">
            {job.usage_instructions}
          </div>
        </div>
      )}

      {/* Fallback for CLI apps without AI usage instructions */}
      {isCli && !hasUsage && (
        <div className="space-y-1.5">
          <p className="text-text-secondary text-xs font-semibold">CLI Application</p>
          <p className="text-text-muted text-xs">Open the Terminal tab to interact with the app.</p>
          {job.start_command && (
            <code className="block text-xs text-accent-green font-[family-name:var(--font-mono)] bg-bg-primary rounded px-2 py-1.5 break-all">
              {job.start_command}
            </code>
          )}
        </div>
      )}
    </div>
  );
}
