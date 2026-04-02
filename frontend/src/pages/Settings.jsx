import { useState, useEffect } from 'react';
import client from '../api/client';

export default function Settings() {
  const [providers, setProviders] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [currentConfig, setCurrentConfig] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [saveMsg, setSaveMsg] = useState('');
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    Promise.all([
      client.get('/settings/providers'),
      client.get('/settings/ai'),
    ]).then(([provRes, aiRes]) => {
      setProviders(provRes.data);
      setCurrentConfig(aiRes.data);
      if (aiRes.data.provider) {
        setSelectedProvider(aiRes.data.provider);
        setSelectedModel(aiRes.data.model || '');
      }
    });
  }, []);

  const activeProvider = providers.find(p => p.id === selectedProvider);
  const freeProviders = providers.filter(p => p.free);
  const paidProviders = providers.filter(p => !p.free);

  const handleTest = async () => {
    if (!selectedProvider || !apiKey) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await client.post('/settings/ai/test', {
        provider: selectedProvider,
        api_key: apiKey,
        model: selectedModel || undefined,
      });
      setTestResult(res.data);
    } catch (err) {
      setTestResult({ success: false, error: err.message });
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await client.delete('/settings/ai');
      setCurrentConfig({ provider: null, model: null, has_key: false });
      setSelectedProvider('');
      setApiKey('');
      setSelectedModel('');
      setTestResult(null);
      setSaveMsg('AI configuration removed.');
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`);
    } finally {
      setDeleting(false);
    }
  };

  const handleSave = async () => {
    if (!selectedProvider || !apiKey) return;
    setSaving(true);
    setSaveMsg('');
    try {
      await client.post('/settings/ai', {
        provider: selectedProvider,
        api_key: apiKey,
        model: selectedModel || undefined,
      });
      setSaveMsg('Settings saved successfully!');
      setCurrentConfig({ provider: selectedProvider, model: selectedModel, has_key: true });
    } catch (err) {
      setSaveMsg(`Error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-6 pt-8 pb-12">
      <h1 className="text-2xl font-bold text-text-primary mb-2">AI Settings</h1>
      <p className="text-text-muted text-sm mb-6">
        Configure which AI provider GitDeploy uses for repo analysis, Dockerfile generation, and self-healing builds.
      </p>

      {/* Current status */}
      {currentConfig && (
        <div className={`mb-6 px-4 py-3 rounded-lg border ${
          currentConfig.has_key
            ? 'bg-accent-green/10 border-accent-green/30'
            : 'bg-accent-red/10 border-accent-red/30'
        }`}>
          {currentConfig.has_key ? (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-accent-green" />
                <span className="text-accent-green text-sm font-medium">
                  AI configured: {providers.find(p => p.id === currentConfig.provider)?.name || currentConfig.provider}
                  {currentConfig.model && ` — ${currentConfig.model}`}
                </span>
              </div>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="text-xs text-accent-red/70 hover:text-accent-red transition-colors disabled:opacity-40"
              >
                {deleting ? 'Removing...' : 'Remove'}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-accent-red" />
              <span className="text-accent-red text-sm font-medium">
                AI not configured — deployments that need AI analysis will fail
              </span>
            </div>
          )}
        </div>
      )}

      {/* Provider selection */}
      <div className="space-y-6">
        {/* Free providers */}
        <div>
          <p className="text-text-secondary text-xs uppercase tracking-wider mb-3 flex items-center gap-2">
            Free Providers
            <span className="text-accent-green text-[10px] bg-accent-green/10 px-1.5 py-0.5 rounded">No cost</span>
          </p>
          <div className="grid grid-cols-2 gap-2">
            {freeProviders.map(p => (
              <button
                key={p.id}
                onClick={() => {
                  setSelectedProvider(p.id);
                  setSelectedModel(p.models[0]?.id || '');
                  setTestResult(null);
                  setSaveMsg('');
                }}
                className={`text-left px-3 py-2.5 rounded-lg border transition-all ${
                  selectedProvider === p.id
                    ? 'border-accent-green bg-accent-green/10 text-text-primary'
                    : 'border-border bg-bg-surface text-text-secondary hover:border-text-muted'
                }`}
              >
                <span className="text-sm font-medium">{p.name}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Paid providers */}
        <div>
          <p className="text-text-secondary text-xs uppercase tracking-wider mb-3">Paid Providers</p>
          <div className="grid grid-cols-2 gap-2">
            {paidProviders.map(p => (
              <button
                key={p.id}
                onClick={() => {
                  setSelectedProvider(p.id);
                  setSelectedModel(p.models[0]?.id || '');
                  setTestResult(null);
                  setSaveMsg('');
                }}
                className={`text-left px-3 py-2.5 rounded-lg border transition-all ${
                  selectedProvider === p.id
                    ? 'border-accent-green bg-accent-green/10 text-text-primary'
                    : 'border-border bg-bg-surface text-text-secondary hover:border-text-muted'
                }`}
              >
                <span className="text-sm font-medium">{p.name}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Configuration form */}
        {activeProvider && (
          <div className="bg-bg-surface border border-border rounded-lg p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-text-primary font-semibold">{activeProvider.name}</h2>
              <a
                href={activeProvider.key_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-accent-green hover:underline"
              >
                Get API Key →
              </a>
            </div>

            {/* API Key */}
            <div>
              <label className="block text-text-muted text-xs mb-1.5">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => { setApiKey(e.target.value); setTestResult(null); setSaveMsg(''); }}
                placeholder={currentConfig?.has_key && currentConfig?.provider === selectedProvider ? '••••••••  (key saved — enter new to replace)' : 'Paste your API key'}
                className="w-full bg-bg-primary border border-border rounded px-3 py-2 text-sm text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-green font-[family-name:var(--font-mono)]"
              />
            </div>

            {/* Model selector */}
            {activeProvider.models.length > 1 && (
              <div>
                <label className="block text-text-muted text-xs mb-1.5">Model</label>
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  className="w-full bg-bg-primary border border-border rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-green"
                >
                  {activeProvider.models.map(m => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                </select>
              </div>
            )}

            {/* Test result */}
            {testResult && (
              <div className={`px-3 py-2 rounded text-xs ${
                testResult.success
                  ? 'bg-accent-green/10 text-accent-green border border-accent-green/30'
                  : 'bg-accent-red/10 text-accent-red border border-accent-red/30'
              }`}>
                {testResult.success ? 'Connection successful!' : `Error: ${testResult.error}`}
              </div>
            )}

            {/* Save message */}
            {saveMsg && (
              <div className={`px-3 py-2 rounded text-xs ${
                saveMsg.startsWith('Error')
                  ? 'bg-accent-red/10 text-accent-red border border-accent-red/30'
                  : 'bg-accent-green/10 text-accent-green border border-accent-green/30'
              }`}>
                {saveMsg}
              </div>
            )}

            {/* Buttons */}
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleTest}
                disabled={!apiKey || testing}
                className="px-4 py-2 text-xs border border-border text-text-secondary rounded hover:border-text-muted transition-colors disabled:opacity-40"
              >
                {testing ? 'Testing...' : 'Test Connection'}
              </button>
              <button
                onClick={handleSave}
                disabled={!apiKey || saving}
                className="px-4 py-2 text-xs bg-accent-green text-bg-primary font-semibold rounded hover:bg-accent-green/90 transition-colors disabled:opacity-40"
              >
                {saving ? 'Saving...' : 'Save Settings'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
