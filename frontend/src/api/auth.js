/**
 * Get the stored JWT token for WebSocket connections.
 */
export function getToken() {
  return localStorage.getItem('token');
}

export function getUsername() {
  return localStorage.getItem('username');
}

export function setAuth(token, username) {
  localStorage.setItem('token', token);
  localStorage.setItem('username', username);
}

export function clearAuth() {
  localStorage.removeItem('token');
  localStorage.removeItem('username');
}

export function isAuthenticated() {
  return !!localStorage.getItem('token');
}
