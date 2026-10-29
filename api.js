let csrf = '';
export function setCsrf(token) { csrf = token || ''; }
export async function api(path, options = {}) {
  const { method = 'GET', body, ...rest } = options;
  const headers = { ...(options.headers || {}) };
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const res = await fetch('/api' + path, { ...rest, method, headers, credentials: 'same-origin', body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)) });
  const type = res.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await res.json() : null;
  if (!res.ok) throw new Error(data?.error || `Something went wrong (${res.status}).`);
  if (data?.csrf) setCsrf(data.csrf);
  return data;
}
