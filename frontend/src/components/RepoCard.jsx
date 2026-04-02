import StatusBadge from './StatusBadge';

const SOURCE_LABELS = {
  readme: 'README',
  config_file: 'Config',
  ai_generated: 'AI Generated',
  template: 'Template',
};

export default function RepoCard({ job }) {
  if (!job) return null;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-text-muted text-xs mb-1">{job.repo_owner}</p>
          <h3 className="text-text-primary font-semibold text-lg">{job.repo_name || 'Unknown'}</h3>
        </div>
        <StatusBadge status={job.status} />
      </div>

      <div className="flex flex-wrap gap-2 mt-3">
        {job.detected_stack && (
          <span className="px-2 py-0.5 text-xs rounded bg-bg-elevated text-accent-blue border border-border">
            {job.detected_stack}
          </span>
        )}
        {job.install_source && (
          <span className="px-2 py-0.5 text-xs rounded bg-bg-elevated text-accent-amber border border-border">
            {SOURCE_LABELS[job.install_source] || job.install_source}
          </span>
        )}
        {job.ai_confidence != null && (
          <span className="px-2 py-0.5 text-xs rounded bg-bg-elevated text-text-muted border border-border">
            AI: {Math.round(job.ai_confidence * 100)}%
          </span>
        )}
      </div>

      {job.commands_run && job.commands_run.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <p className="text-text-muted text-xs mb-2 uppercase tracking-wide">Commands</p>
          <div className="space-y-1">
            {job.commands_run.map((cmd, i) => (
              <div key={i} className="flex items-center gap-2 text-xs font-[family-name:var(--font-mono)]">
                <span className="text-accent-green">$</span>
                <span className="text-text-secondary">{cmd}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
