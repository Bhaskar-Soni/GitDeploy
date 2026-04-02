import { useState } from 'react';

export default function AppViewer({ proxyUrl, appPort, jobId }) {
  const [isFullscreen, setIsFullscreen] = useState(false);

  if (!proxyUrl) return null;

  // Always proxy through backend to strip X-Frame-Options headers
  const iframeSrc = `/api/proxy/${jobId}/`;

  return (
    <div className={`border border-border rounded-lg overflow-hidden ${isFullscreen ? 'fixed inset-0 z-50 bg-bg-primary' : ''}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-bg-surface border-b border-border">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-accent-green animate-pulse" />
          <span className="text-text-secondary text-xs font-[family-name:var(--font-mono)]">
            Live on port {appPort}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={proxyUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-accent-blue hover:underline"
          >
            Open in new tab
          </a>
          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
          </button>
        </div>
      </div>

      {/* App iframe — proxied through backend to strip X-Frame-Options */}
      <iframe
        src={iframeSrc}
        className="w-full bg-white"
        style={{ height: isFullscreen ? 'calc(100vh - 40px)' : '600px' }}
        title="Running Application"
      />
    </div>
  );
}
