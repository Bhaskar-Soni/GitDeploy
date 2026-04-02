import { useState, useEffect, useRef, useCallback } from 'react';
import { getToken } from '../api/auth';

const MAX_RECONNECT_ATTEMPTS = 3;
const BASE_DELAY = 1000;

export default function useJobSocket(jobId) {
  const [logs, setLogs] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [status, setStatus] = useState(null);
  const wsRef = useRef(null);
  const attemptRef = useRef(0);
  // Ref mirrors isComplete so the onclose closure always sees the latest value
  const isCompleteRef = useRef(false);

  const connect = useCallback(() => {
    if (!jobId || isCompleteRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const wsUrl = `${protocol}//${window.location.host}/ws/jobs/${jobId}/logs${token ? `?token=${token}` : ''}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      attemptRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs((prev) => [...prev, data]);

        if (data.final) {
          isCompleteRef.current = true;
          setIsComplete(true);
          setStatus(data.message);
          ws.close();
        } else if (data.running) {
          isCompleteRef.current = true;
          setIsComplete(true);
          setStatus('running');
          ws.close();
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;

      // Reconnect with exponential backoff only if not yet complete
      if (!isCompleteRef.current && attemptRef.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = BASE_DELAY * Math.pow(2, attemptRef.current);
        attemptRef.current += 1;
        setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [jobId]);

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { logs, isConnected, isComplete, status };
}
