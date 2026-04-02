import { useState } from 'react';

export default function DeleteConfirmModal({ job, onConfirm, onCancel }) {
  const [confirmText, setConfirmText] = useState('');
  const repoName = `${job.repo_owner}/${job.repo_name}`;
  const requiresTyping = true;
  const isConfirmed = confirmText === 'DELETE';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* Modal */}
      <div className="relative bg-bg-surface border border-accent-red/40 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 pt-6 pb-4">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-10 h-10 rounded-full bg-accent-red/15 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-accent-red" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </div>
            <div>
              <h3 className="text-lg font-semibold text-text-primary">Delete Job</h3>
              <p className="text-text-muted text-sm">This action cannot be undone</p>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="px-6 pb-4 space-y-3">
          <p className="text-text-secondary text-sm">
            The following resources will be permanently removed:
          </p>

          <div className="bg-bg-primary rounded-lg border border-border p-3 space-y-2">
            {/* Repo */}
            <div className="flex items-start gap-2">
              <span className="text-accent-red text-xs mt-0.5">x</span>
              <div>
                <p className="text-text-primary text-sm font-medium">{repoName}</p>
                <p className="text-text-muted text-xs">Cloned repository files</p>
              </div>
            </div>

            {/* Container */}
            {job.app_container_id && (
              <div className="flex items-start gap-2">
                <span className="text-accent-red text-xs mt-0.5">x</span>
                <div>
                  <p className="text-text-primary text-sm font-[family-name:var(--font-mono)]">
                    {job.app_container_id.substring(0, 12)}
                  </p>
                  <p className="text-text-muted text-xs">Docker container</p>
                </div>
              </div>
            )}

            {/* Image */}
            {job.docker_image && (
              <div className="flex items-start gap-2">
                <span className="text-accent-red text-xs mt-0.5">x</span>
                <div>
                  <p className="text-text-primary text-sm font-[family-name:var(--font-mono)]">
                    {job.docker_image}
                  </p>
                  <p className="text-text-muted text-xs">Docker image</p>
                </div>
              </div>
            )}

            {/* Databases */}
            {job.databases && job.databases.length > 0 && job.databases.map((db) => (
              <div key={db.id} className="flex items-start gap-2">
                <span className="text-accent-red text-xs mt-0.5">x</span>
                <div>
                  <p className="text-text-primary text-sm font-[family-name:var(--font-mono)]">
                    {db.container_name || db.db_type}
                  </p>
                  <p className="text-text-muted text-xs">Database container ({db.db_type})</p>
                </div>
              </div>
            ))}
          </div>

          {/* Confirmation input */}
          <div>
            <p className="text-text-muted text-xs mb-2">
              Type <span className="text-accent-red font-semibold font-[family-name:var(--font-mono)]">DELETE</span> to confirm
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="DELETE"
              className="w-full px-3 py-2 bg-bg-primary border border-border rounded-lg text-text-primary text-sm font-[family-name:var(--font-mono)] placeholder:text-text-muted/40 focus:outline-none focus:border-accent-red/50 focus:ring-1 focus:ring-accent-red/30"
              autoFocus
            />
          </div>
        </div>

        {/* Actions */}
        <div className="px-6 pb-6 flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 text-sm border border-border text-text-secondary rounded-lg hover:bg-bg-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!isConfirmed}
            className="flex-1 px-4 py-2.5 text-sm bg-accent-red text-white font-semibold rounded-lg hover:bg-accent-red/90 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Delete Permanently
          </button>
        </div>
      </div>
    </div>
  );
}
