const STATUS_CONFIG = {
  queued: { label: 'Queued', color: 'bg-text-muted', pulse: false },
  cloning: { label: 'Cloning', color: 'bg-accent-amber', pulse: true },
  analyzing: { label: 'Analyzing', color: 'bg-accent-amber', pulse: true },
  provisioning_db: { label: 'Provisioning DB', color: 'bg-accent-cyan', pulse: true },
  installing: { label: 'Installing', color: 'bg-accent-amber', pulse: true },
  running: { label: 'Running', color: 'bg-accent-green', pulse: true },
  success: { label: 'Success', color: 'bg-accent-green', pulse: false },
  failed: { label: 'Failed', color: 'bg-accent-red', pulse: false },
  timeout: { label: 'Timeout', color: 'bg-accent-red', pulse: false },
};

export default function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.queued;

  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium">
      <span className={`relative inline-block w-2 h-2 rounded-full ${config.color}`}>
        {config.pulse && (
          <span className={`absolute inset-0 rounded-full ${config.color} animate-ping opacity-75`} />
        )}
      </span>
      <span className="text-text-secondary">{config.label}</span>
    </span>
  );
}
