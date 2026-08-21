import { getCookie, setCookie } from './auth';

export const API_URL = '';

function getHeaders() {
  const token = getCookie('cal11_active_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function checkUser(username?: string) {
  const url = username ? `${API_URL}/api/check?username=${encodeURIComponent(username)}` : `${API_URL}/api/check`;
  const res = await fetch(url, { headers: getHeaders() });
  return res.json();
}

export async function registerUser(username: string, pin?: string) {
  const res = await fetch(`${API_URL}/api/register`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username, pin }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Register failed');
  }
  const data = await res.json();
  if (data.sessionToken) {
    setCookie('cal11_active_token', data.sessionToken, 7);
  }
  return data;
}

export async function loginUser(username: string, pin?: string) {
  const res = await fetch(`${API_URL}/api/login`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username, pin }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Login failed');
  }
  const data = await res.json();
  if (data.sessionToken) {
    setCookie('cal11_active_token', data.sessionToken, 7);
  }
  return data;
}

export async function loginWithSessionToken(username: string, sessionToken: string) {
  const res = await fetch(`${API_URL}/api/login`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username, sessionToken }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Session ungültig');
  }
  const data = await res.json();
  if (data.sessionToken) {
    setCookie('cal11_active_token', data.sessionToken, 7);
  }
  return data;
}

export async function fetchUser(username: string) {
  const res = await fetch(`${API_URL}/api/users/${username}`, { headers: getHeaders() });
  if (!res.ok) throw new Error('User not found');
  return res.json();
}

export async function saveUserSettings(username: string, data: any) {
  const res = await fetch(`${API_URL}/api/users/${username}`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Speichern fehlgeschlagen');
  }
  return res.json();
}

export async function uploadFile(filename: string, mimeType: string, data: string) {
  const res = await fetch(`${API_URL}/api/upload`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ filename, mimeType, data }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Upload fehlgeschlagen');
  }
  return res.json();
}

export async function fetchEvents() {
  const res = await fetch(`${API_URL}/api/events`, { headers: getHeaders() });
  return res.json();
}

export async function createEvent(eventData: any) {
  const res = await fetch(`${API_URL}/api/events`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(eventData),
  });
  return res.json();
}

export async function updateEvent(id: string, eventData: any) {
  const res = await fetch(`${API_URL}/api/events/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(eventData),
  });
  return res.json();
}

export async function deleteEvent(id: string) {
  const res = await fetch(`${API_URL}/api/events/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  return res.json();
}

export async function submitFeedback(username: string, text: string) {
  const res = await fetch(`${API_URL}/api/feedback`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username, text }),
  });
  if (!res.ok) throw new Error('Failed to submit feedback');
  return res.json();
}

export async function fetchCourses() {
  const res = await fetch(`${API_URL}/api/courses`, { headers: getHeaders() });
  return res.json();
}

export async function saveCourse(courseData: { id?: string; name: string; teacher: string; type: 'LK' | 'GK' | 'AG' }) {
  if (courseData.id) {
    const res = await fetch(`${API_URL}/api/courses/${courseData.id}`, {
      method: 'PUT',
      headers: getHeaders(),
      body: JSON.stringify(courseData),
    });
    return res.json();
  }
  const res = await fetch(`${API_URL}/api/courses`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(courseData),
  });
  return res.json();
}

export async function reorderCourses(courseIds: string[]) {
  const res = await fetch(`${API_URL}/api/courses/reorder`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify({ courseIds }),
  });
  return res.json();
}

export async function deleteCourse(id: string) {
  const res = await fetch(`${API_URL}/api/courses/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  return res.json();
}

export async function resetCoursesToDefaults() {
  const res = await fetch(`${API_URL}/api/courses/reset-defaults`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Fehler beim Zurücksetzen der Kurse');
  }
  return res.json();
}

export async function fetchAdmins() {
  const res = await fetch(`${API_URL}/api/admins`, { headers: getHeaders() });
  return res.json();
}

export async function addAdmin(username: string) {
  const res = await fetch(`${API_URL}/api/admins`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Fehler beim Hinzufügen des Admins');
  }
  return res.json();
}

export async function removeAdmin(username: string) {
  const res = await fetch(`${API_URL}/api/admins/${username}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Fehler beim Entfernen des Admins');
  }
  return res.json();
}

export async function adminFetchUsers() {
  const res = await fetch(`${API_URL}/api/admin/users`, { headers: getHeaders() });
  return res.json();
}

export async function adminUpdateUserStatus(username: string, status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED') {
  const res = await fetch(`${API_URL}/api/admin/users/${username}/status`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify({ status })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminAddUser(username: string, pin?: string) {
  const res = await fetch(`${API_URL}/api/admin/users`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ username, pin })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminDeleteUser(username: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}`, {
    method: 'DELETE',
    headers: getHeaders()
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminResetUserPin(username: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}/reset-pin`, {
    method: 'PUT',
    headers: getHeaders()
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchCategories() {
  const res = await fetch(`${API_URL}/api/categories`, { headers: getHeaders() });
  return res.json();
}

export async function saveCategory(category: { id?: string; name: string; color: string; sort_order?: number }) {
  if (category.id) {
    const res = await fetch(`${API_URL}/api/categories/${category.id}`, {
      method: 'PUT',
      headers: getHeaders(),
      body: JSON.stringify(category),
    });
    return res.json();
  }
  const res = await fetch(`${API_URL}/api/categories`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(category),
  });
  return res.json();
}

export async function deleteCategory(id: string) {
  const res = await fetch(`${API_URL}/api/categories/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  return res.json();
}

export async function reorderCategories(categoryIds: string[]) {
  const res = await fetch(`${API_URL}/api/categories/reorder`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify({ categoryIds }),
  });
  return res.json();
}
