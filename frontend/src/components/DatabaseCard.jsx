import { useState } from 'react';

const DB_COLORS = {
  postgresql: 'text-accent-green border-accent-green/30',
  mysql: 'text-accent-amber border-accent-amber/30',
  mariadb: 'text-accent-amber border-accent-amber/30',
  mongodb: 'text-accent-red border-accent-red/30',
  redis: 'text-accent-blue border-accent-blue/30',
};

export default function DatabaseCard({ database }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!database) return null;

  const colorClass = DB_COLORS[database.db_type] || 'text-text-secondary border-border';
  const dbUrl = database.env_vars?.DATABASE_URL || database.env_vars?.MONGODB_URI || database.env_vars?.REDIS_URL || '';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(dbUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard not available
    }
  };

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 mt-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 text-xs font-semibold rounded border ${colorClass}`}>
            {database.db_type?.toUpperCase()}
          </span>
          <span className="text-text-muted text-xs">
            {database.detection_source === 'static_scan' ? 'Auto-detected' :
             database.detection_source === 'ai_advised' ? 'AI Recommended' : 'README'}
          </span>
        </div>
        <span className={`text-xs ${database.status === 'ready' ? 'text-accent-green' : 'text-text-muted'}`}>
          {database.status}
        </span>
      </div>

      {database.container_name && (
        <p className="text-text-muted text-xs font-[family-name:var(--font-mono)] mb-2">
          {database.container_name}
        </p>
      )}

      <div className="flex items-center gap-2 mt-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-accent-blue hover:text-accent-blue/80 transition-colors"
        >
          {expanded ? 'Hide' : 'Show'} Connection Info
        </button>
        {dbUrl && (
          <button
            onClick={handleCopy}
            className="text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            {copied ? 'Copied!' : 'Copy URL'}
          </button>
        )}
      </div>

      {expanded && database.env_vars && (
        <div className="mt-3 bg-bg-primary rounded p-3 border border-border">
          <div className="space-y-1">
            {Object.entries(database.env_vars).map(([key, value]) => (
              <div key={key} className="flex text-xs font-[family-name:var(--font-mono)] gap-1">
                <span className="text-accent-cyan shrink-0">{key}</span>
                <span className="text-text-muted">=</span>
                <span className="text-text-secondary break-all">{value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
