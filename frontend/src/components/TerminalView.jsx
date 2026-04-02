import { useEffect, useRef, useState } from 'react';
import useJobSocket from '../hooks/useJobSocket';

const STREAM_COLORS = {
  stdout: 'text-accent-green',
  stderr: 'text-accent-red',
  system: 'text-text-muted',
};

function isProvisioningMessage(msg) {
  const keywords = ['Provisioning', 'ready at', 'Database:', 'Network created', 'Credentials injected', 'isolated network'];
  return keywords.some((k) => msg.includes(k));
}

export default function TerminalView({ jobId, initialLogs = [], onComplete }) {
  const { logs: wsLogs, isConnected, isComplete } = useJobSocket(jobId);
  const [scrollLock, setScrollLock] = useState(false);
  const containerRef = useRef(null);

  const allLogs = [...initialLogs, ...wsLogs];

  useEffect(() => {
    if (isComplete && onComplete) {
      onComplete();
    }
  }, [isComplete, onComplete]);

  useEffect(() => {
    if (!scrollLock && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [allLogs.length, scrollLock]);

  const handleCopyAll = async () => {
    const text = allLogs.map((l) => l.message).join('\n');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard not available
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg-primary rounded-lg border border-border overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-2 bg-bg-surface border-b border-border">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-accent-red/60" />
            <span className="w-3 h-3 rounded-full bg-accent-amber/60" />
            <span className="w-3 h-3 rounded-full bg-accent-green/60" />
          </div>
          <span className="text-text-muted text-xs font-[family-name:var(--font-mono)]">
            {isConnected ? 'connected' : isComplete ? 'completed' : 'connecting...'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setScrollLock(!scrollLock)}
            className={`text-xs px-2 py-0.5 rounded transition-colors ${
              scrollLock
                ? 'bg-accent-amber/20 text-accent-amber'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {scrollLock ? 'Scroll Locked' : 'Auto-scroll'}
          </button>
          <button
            onClick={handleCopyAll}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors px-2 py-0.5"
          >
            Copy All
          </button>
        </div>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto p-3 font-[family-name:var(--font-mono)] text-sm leading-relaxed"
        style={{ minHeight: '400px', maxHeight: '70vh' }}
      >
        {allLogs.length === 0 && (
          <div className="text-text-muted animate-pulse">Waiting for logs...</div>
        )}
        {allLogs.map((log, i) => {
          const isProvisioning = log.stream === 'system' && isProvisioningMessage(log.message);
          const colorClass = isProvisioning ? 'text-accent-cyan' : STREAM_COLORS[log.stream] || 'text-text-muted';

          return (
            <div key={i} className={`${colorClass} group flex`}>
              <span className="opacity-0 group-hover:opacity-100 text-text-muted text-xs mr-3 shrink-0 transition-opacity select-none">
                {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
              </span>
              <span className="break-all">{log.message}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
