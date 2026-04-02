import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { getToken } from '../api/auth';

export default function WebTerminal({ jobId }) {
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitAddonRef = useRef(null);
  const wsRef = useRef(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!jobId || !containerRef.current) return;

    // Initialize xterm.js
    const term = new Terminal({
      theme: {
        background: '#0d0d0d',
        foreground: '#e2e8f0',
        cursor: '#4ade80',
        selectionBackground: '#4ade8040',
        black: '#1a1a1a',
        brightBlack: '#4a5568',
        red: '#fc8181',
        brightRed: '#fc8181',
        green: '#4ade80',
        brightGreen: '#4ade80',
        yellow: '#fbbf24',
        brightYellow: '#fbbf24',
        blue: '#60a5fa',
        brightBlue: '#60a5fa',
        magenta: '#c084fc',
        brightMagenta: '#c084fc',
        cyan: '#22d3ee',
        brightCyan: '#22d3ee',
        white: '#e2e8f0',
        brightWhite: '#ffffff',
      },
      fontFamily: 'Consolas, "Courier New", monospace',
      fontSize: 13,
      lineHeight: 1.4,
      cursorBlink: true,
      allowProposedApi: true,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());

    term.open(containerRef.current);
    fitAddon.fit();

    termRef.current = term;
    fitAddonRef.current = fitAddon;

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/jobs/${jobId}/terminal${token ? `?token=${token}` : ''}`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'output' || data.type === 'connected') {
          term.write(data.data);
        } else if (data.type === 'error') {
          term.write(`\x1b[31m${data.data}\x1b[0m\r\n`);
        }
      } catch {}
    };

    ws.onclose = () => setConnected(false);
    ws.onerror = () => ws.close();

    // Send keystrokes to container
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    // Notify backend on resize
    const handleResize = () => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'resize',
          cols: term.cols,
          rows: term.rows,
        }));
      }
    };

    const observer = new ResizeObserver(handleResize);
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      ws.close();
      term.dispose();
      wsRef.current = null;
      termRef.current = null;
    };
  }, [jobId]);

  return (
    <div className="border border-border rounded-lg overflow-hidden flex flex-col" style={{ height: '500px' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-bg-surface border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-accent-red/60" />
            <span className="w-3 h-3 rounded-full bg-accent-amber/60" />
            <span className="w-3 h-3 rounded-full bg-accent-green/60" />
          </div>
          <span className="text-text-muted text-xs font-[family-name:var(--font-mono)]">
            {connected ? 'bash - interactive terminal' : 'connecting...'}
          </span>
        </div>
        <span className={`w-2 h-2 rounded-full ${connected ? 'bg-accent-green' : 'bg-accent-red'}`} />
      </div>

      {/* xterm.js terminal */}
      <div ref={containerRef} className="flex-1 overflow-hidden p-1" style={{ background: '#0d0d0d' }} />
    </div>
  );
}
