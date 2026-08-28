export const API_URL = '';

function getHeaders(adminToken?: string) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  if (adminToken) headers['X-Admin-Token'] = adminToken;
  return headers;
}

export async function checkUser(username?: string) {
  const url = username ? `${API_URL}/api/check?username=${encodeURIComponent(username)}` : `${API_URL}/api/check`;
  const res = await fetch(url, { headers: getHeaders() });
  return res.json();
}

export async function fetchCurrentSession() {
  const res = await fetch(`${API_URL}/api/session`, { headers: getHeaders(), credentials: 'same-origin' });
  if (!res.ok) return null;
  return res.json();
}

export async function hasActiveSession(): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/session`, {
    headers: getHeaders(), credentials: 'same-origin', cache: 'no-store'
  });
  if (res.ok) return true;
  if (res.status === 401 || res.status === 403) return false;
  throw new Error(`Sessionstatus vorübergehend nicht verfügbar (${res.status})`);
}

export async function logoutSession() {
  await fetch(`${API_URL}/api/logout`, { method: 'POST', headers: getHeaders(), credentials: 'same-origin' });
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

export async function uploadFile(filename: string, mimeType: string, data: string, privateAttachment = false) {
  const res = await fetch(`${API_URL}/api/upload`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ filename, mimeType, data, privateAttachment }),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.error || 'Upload fehlgeschlagen');
  }
  return res.json();
}

export async function fetchEvents() {
  const res = await fetch(`${API_URL}/api/events`, { headers: getHeaders() });
  if (!res.ok) throw new Error((await res.json()).error || 'Termine konnten nicht geladen werden.');
  return res.json();
}

export async function createEvent(eventData: any) {
  const res = await fetch(`${API_URL}/api/events`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(eventData),
  });
  if (!res.ok) throw new Error((await res.json()).error || 'Termin konnte nicht erstellt werden.');
  return res.json();
}

export async function updateEvent(id: string, eventData: any) {
  const res = await fetch(`${API_URL}/api/events/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(eventData),
  });
  if (!res.ok) throw new Error((await res.json()).error || 'Termin konnte nicht geändert werden.');
  return res.json();
}

export async function deleteEvent(id: string) {
  const res = await fetch(`${API_URL}/api/events/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!res.ok) throw new Error((await res.json()).error || 'Termin konnte nicht gelöscht werden.');
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

export async function saveCourse(courseData: { id?: string; name: string; teacher: string; type: 'LK' | 'GK' | 'AG' }, adminToken?: string) {
  if (courseData.id) {
    const res = await fetch(`${API_URL}/api/courses/${courseData.id}`, {
      method: 'PUT',
      headers: getHeaders(adminToken),
      body: JSON.stringify(courseData),
    });
    return res.json();
  }
  const res = await fetch(`${API_URL}/api/courses`, {
    method: 'POST',
    headers: getHeaders(adminToken),
    body: JSON.stringify(courseData),
  });
  return res.json();
}

export async function reorderCourses(courseIds: string[], adminToken?: string) {
  const res = await fetch(`${API_URL}/api/courses/reorder`, {
    method: 'PUT',
    headers: getHeaders(adminToken),
    body: JSON.stringify({ courseIds }),
  });
  return res.json();
}

export async function deleteCourse(id: string, adminToken?: string) {
  const res = await fetch(`${API_URL}/api/courses/${id}`, {
    method: 'DELETE',
    headers: getHeaders(adminToken),
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

export async function adminElevate(password: string) {
  const res = await fetch(`${API_URL}/api/admin/elevate`, {
    method: 'POST',
    headers: getHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ password })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Admin-Passwort wurde abgelehnt.');
  return data as { adminToken: string; expiresIn: number };
}

export async function adminFetchUsers(adminToken?: string) {
  const res = await fetch(`${API_URL}/api/admin/users`, { headers: getHeaders(adminToken) });
  return res.json();
}

export async function adminUpdateUserStatus(username: string, status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED', adminToken?: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}/status`, {
    method: 'PUT',
    headers: getHeaders(adminToken),
    body: JSON.stringify({ status })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminAddUser(username: string, pin?: string, adminToken?: string, vpOnly = false, className?: string) {
  const res = await fetch(`${API_URL}/api/admin/users`, {
    method: 'POST',
    headers: getHeaders(adminToken),
    body: JSON.stringify({ username, pin, vpOnly, className })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminDeleteUser(username: string, adminToken?: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}`, {
    method: 'DELETE',
    headers: getHeaders(adminToken)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminResetUserPin(username: string, adminToken?: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}/reset-pin`, {
    method: 'PUT',
    headers: getHeaders(adminToken)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function adminSetUserPin(username: string, pin: string, adminToken?: string) {
  const res = await fetch(`${API_URL}/api/admin/users/${username}/pin`, {
    method: 'PUT',
    headers: getHeaders(adminToken),
    body: JSON.stringify({ pin })
  });
  if (!res.ok) {
    const error = await res.json().catch(async () => ({ error: await res.text() }));
    throw new Error(error.error || 'PIN konnte nicht gesetzt werden.');
  }
  return res.json();
}

export async function fetchCategories() {
  const res = await fetch(`${API_URL}/api/categories`, { headers: getHeaders() });
  return res.json();
}

export async function createPrivateCategory(name: string, color: string) {
  const res = await fetch(`${API_URL}/api/private-categories`, {
    method: 'POST', headers: getHeaders(), body: JSON.stringify({ name, color })
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Kategorie konnte nicht erstellt werden.');
  return data;
}

export async function deletePrivateCategory(id: string) {
  const res = await fetch(`${API_URL}/api/private-categories/${encodeURIComponent(id)}`, {
    method: 'DELETE', headers: getHeaders()
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Kategorie konnte nicht gelöscht werden.');
  return data;
}

export async function saveCategory(category: { id?: string; name: string; color: string; sort_order?: number }, adminToken?: string) {
  if (category.id) {
    const res = await fetch(`${API_URL}/api/categories/${category.id}`, {
      method: 'PUT',
      headers: getHeaders(adminToken),
      body: JSON.stringify(category),
    });
    return res.json();
  }
  const res = await fetch(`${API_URL}/api/categories`, {
    method: 'POST',
    headers: getHeaders(adminToken),
    body: JSON.stringify(category),
  });
  return res.json();
}

export async function deleteCategory(id: string, adminToken?: string) {
  const res = await fetch(`${API_URL}/api/categories/${id}`, {
    method: 'DELETE',
    headers: getHeaders(adminToken),
  });
  return res.json();
}

export async function reorderCategories(categoryIds: string[], adminToken?: string) {
  const res = await fetch(`${API_URL}/api/categories/reorder`, {
    method: 'PUT',
    headers: getHeaders(adminToken),
    body: JSON.stringify({ categoryIds }),
  });
  return res.json();
}
