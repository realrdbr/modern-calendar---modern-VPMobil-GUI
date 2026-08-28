import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  adminFetchUsers,
  adminUpdateUserStatus,
  adminResetUserPin,
  adminSetUserPin,
  adminAddUser,
  adminDeleteUser,
  adminFetchVpUsers,
  adminAddVpUser,
  adminUpdateVpUserStatus,
  adminUpdateVpUserPin,
  adminDeleteVpUser,
  adminVerifyPin,
  fetchCategories,
  saveCategory,
  deleteCategory,
  fetchCourses,
  saveCourse,
  deleteCourse,
  reorderCourses,
  fetchAdmins
} from '../lib/api';
import { EventCategory, Course } from '../types';
import {
  ShieldCheck,
  Search,
  KeyRound,
  Trash2,
  Plus,
  ArrowLeft,
  ArrowRight,
  Users,
  UserPlus,
  RefreshCw,
  LogOut,
  ExternalLink,
  Lock,
  Unlock,
  AlertTriangle,
  BookOpen,
  Palette,
  Moon,
  Sun
} from 'lucide-react';

interface SharedUser {
  username: string;
  status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED' | 'ADMIN';
  isAdmin?: boolean;
}

interface VpUser {
  username: string;
  className: string;
  active: boolean;
  mustChangePin: boolean;
  createdBy: string;
  createdAt: string;
}

export default function AdminPage() {
  const navigate = useNavigate();

  // Theme & Session
  const [isDark, setIsDark] = useState<boolean>(() => {
    return localStorage.getItem('theme') === 'dark' ||
      (!localStorage.getItem('theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
  });

  const [currentUsername, setCurrentUsername] = useState<string>(() => {
    return localStorage.getItem('cal11_user') || '';
  });
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [isPinVerified, setIsPinVerified] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  // Login form state (if not logged in)
  const [loginUser, setLoginUser] = useState('');
  const [loginPin, setLoginPin] = useState('');
  const [loginError, setLoginError] = useState('');

  // PIN challenge state (if logged in as admin)
  const [pinChallengeInput, setPinChallengeInput] = useState('');
  const [pinChallengeError, setPinChallengeError] = useState('');

  // Active Tab
  const [activeTab, setActiveTab] = useState<'users' | 'vp_users' | 'categories' | 'courses'>('users');

  // Data states
  const [users, setUsers] = useState<SharedUser[]>([]);
  const [vpUsers, setVpUsers] = useState<VpUser[]>([]);
  const [categories, setCategories] = useState<EventCategory[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [searchFilter, setSearchFilter] = useState('');

  // Calendar / Shared User Form
  const [newUserName, setNewUserName] = useState('');
  const [newUserPin, setNewUserPin] = useState('');
  const [selectedUsers, setSelectedUsers] = useState<string[]>([]);
  const [pinDialogUser, setPinDialogUser] = useState<string | null>(null);
  const [pinDialogValue, setPinDialogValue] = useState('');

  // VP-Only User Form
  const [newVpUsername, setNewVpUsername] = useState('');
  const [newVpPin, setNewVpPin] = useState('');
  const [newVpClass, setNewVpClass] = useState('11');
  const [vpPinDialogUser, setVpPinDialogUser] = useState<string | null>(null);
  const [vpPinDialogValue, setVpPinDialogValue] = useState('');
  const [vpPinDialogMustChange, setVpPinDialogMustChange] = useState(true);

  // Category Form
  const [newCatName, setNewCatName] = useState('');
  const [newCatColor, setNewCatColor] = useState('#0f766e');

  // Course Form
  const [newCourseName, setNewCourseName] = useState('');
  const [newCourseTeacher, setNewCourseTeacher] = useState('');
  const [newCourseType, setNewCourseType] = useState<'LK' | 'GK' | 'AG'>('GK');

  // Theme styling helpers
  const theme = {
    bg: isDark ? 'bg-[#0f1117] text-gray-100' : 'bg-gray-50 text-gray-900',
    card: isDark ? 'bg-[#181b24] border-gray-800' : 'bg-white border-gray-200',
    cardHeader: isDark ? 'bg-[#1e222e] border-gray-800' : 'bg-gray-50 border-gray-200',
    input: isDark ? 'bg-[#12141c] border-gray-700 text-gray-100 placeholder-gray-500' : 'bg-white border-gray-300 text-gray-900 placeholder-gray-400',
    border: isDark ? 'border-gray-800' : 'border-gray-200',
    hover: isDark ? 'hover:bg-gray-800/60' : 'hover:bg-gray-100',
    muted: isDark ? 'text-gray-400' : 'text-gray-500',
    faint: isDark ? 'text-gray-500' : 'text-gray-400'
  };

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  }, [isDark]);

  useEffect(() => {
    checkAdminAuth();
  }, [currentUsername]);

  const checkAdminAuth = async () => {
    setLoading(true);
    const token = localStorage.getItem('cal11_session');
    if (!token && !currentUsername) {
      setIsAdmin(false);
      setLoading(false);
      return;
    }

    try {
      const admins = await fetchAdmins();
      const adminList: string[] = Array.isArray(admins) ? admins.map((a: string) => a.toLowerCase()) : [];
      const userIsAdmin = currentUsername ? adminList.includes(currentUsername.toLowerCase()) : false;
      setIsAdmin(userIsAdmin);
    } catch {
      setIsAdmin(false);
    } finally {
      setLoading(false);
    }
  };

  const loadAllData = async () => {
    try {
      const [fetchedUsers, fetchedVpUsers, fetchedCats, fetchedCourses] = await Promise.all([
        adminFetchUsers().catch(() => []),
        adminFetchVpUsers().catch(() => []),
        fetchCategories().catch(() => []),
        fetchCourses().catch(() => [])
      ]);
      setUsers(fetchedUsers);
      setVpUsers(fetchedVpUsers);
      setCategories(fetchedCats);
      setCourses(fetchedCourses);
    } catch (err) {
      console.error('Fehler beim Laden der Admin-Daten:', err);
    }
  };

  useEffect(() => {
    if (isAdmin && isPinVerified) {
      loadAllData();
    }
  }, [isAdmin, isPinVerified]);

  // Login handler
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError('');
    const uname = loginUser.trim().toLowerCase();
    if (!uname) {
      setLoginError('Bitte Benutzername eingeben.');
      return;
    }

    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: uname, pin: loginPin })
      });
      const data = await res.json();
      if (!res.ok) {
        setLoginError(data.error || 'Anmeldung fehlgeschlagen.');
        return;
      }

      localStorage.setItem('cal11_user', uname);
      setCurrentUsername(uname);

      const admins = await fetchAdmins();
      const adminList: string[] = Array.isArray(admins) ? admins.map((a: string) => a.toLowerCase()) : [];
      if (!adminList.includes(uname)) {
        setIsAdmin(false);
        setLoginError('Dieser Benutzer hat keine Administrator-Rechte.');
        return;
      }

      setIsAdmin(true);
      setIsPinVerified(true);
    } catch (err: any) {
      setLoginError(err.message || 'Fehler bei der Verbindung zum Server.');
    }
  };

  // PIN challenge handler
  const handlePinChallenge = async (e: React.FormEvent) => {
    e.preventDefault();
    setPinChallengeError('');
    try {
      await adminVerifyPin(pinChallengeInput);
      setIsPinVerified(true);
      setPinChallengeInput('');
    } catch (err: any) {
      setPinChallengeError(err.message || 'Falsche Admin-PIN.');
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('cal11_session');
    localStorage.removeItem('cal11_user');
    setCurrentUsername('');
    setIsAdmin(false);
    setIsPinVerified(false);
  };

  // --- CALENDAR / SHARED USERS ---
  const handleAddSharedUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUserName.trim()) return;
    try {
      await adminAddUser(newUserName.trim(), newUserPin.trim() || undefined);
      setNewUserName('');
      setNewUserPin('');
      const updated = await adminFetchUsers();
      setUsers(updated);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Erstellen des Nutzers');
    }
  };

  const handleDeleteSharedUser = async (uname: string) => {
    if (!confirm(`Möchtest du den Benutzer "${uname}" wirklich löschen? Dies löscht auch das verknüpfte VP-Konto.`)) return;
    try {
      await adminDeleteUser(uname);
      setUsers(prev => prev.filter(u => u.username.toLowerCase() !== uname.toLowerCase()));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Löschen');
    }
  };

  const handleUpdateUserStatus = async (uname: string, status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED') => {
    try {
      await adminUpdateUserStatus(uname, status);
      setUsers(prev => prev.map(u => u.username.toLowerCase() === uname.toLowerCase() ? { ...u, status } : u));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Aktualisieren des Status');
    }
  };

  const handleResetUserPin = async (uname: string) => {
    if (!confirm(`Passwort/PIN für "${uname}" wirklich löschen? Der Benutzer kann sich danach ohne Passwort anmelden.`)) return;
    try {
      await adminResetUserPin(uname);
      alert(`Passwort für "${uname}" wurde erfolgreich zurückgesetzt.`);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Zurücksetzen');
    }
  };

  const handleSaveUserPin = async () => {
    if (!pinDialogUser) return;
    try {
      await adminSetUserPin(pinDialogUser, pinDialogValue.trim());
      alert(`Passwort für "${pinDialogUser}" wurde aktualisiert.`);
      setPinDialogUser(null);
      setPinDialogValue('');
    } catch (err: any) {
      alert(err.message || 'Fehler beim Setzen des Passworts');
    }
  };

  const handleSelectUser = (uname: string) => {
    setSelectedUsers(prev =>
      prev.includes(uname) ? prev.filter(u => u !== uname) : [...prev, uname]
    );
  };

  const handleSelectAllUsers = () => {
    const mutableUsers = users.filter(u => !u.isAdmin && u.status !== 'ADMIN').map(u => u.username);
    if (selectedUsers.length === mutableUsers.length) {
      setSelectedUsers([]);
    } else {
      setSelectedUsers(mutableUsers);
    }
  };

  const handleBulkUpdateStatus = async (status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED') => {
    if (!selectedUsers.length) return;
    try {
      for (const uname of selectedUsers) {
        await adminUpdateUserStatus(uname, status);
      }
      setUsers(prev => prev.map(u => selectedUsers.includes(u.username) ? { ...u, status } : u));
      setSelectedUsers([]);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Aktualisieren der ausgewählten Nutzer');
    }
  };

  // --- VP-ONLY USERS ---
  const handleAddVpUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newVpUsername.trim() || !newVpPin.trim()) {
      alert('Bitte Benutzername und 4-stellige PIN angeben.');
      return;
    }
    try {
      const created = await adminAddVpUser(newVpUsername.trim(), newVpPin.trim(), newVpClass.trim() || '11');
      setNewVpUsername('');
      setNewVpPin('');
      setNewVpClass('11');
      setVpUsers(prev => [created, ...prev]);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Erstellen des VP-Nutzers');
    }
  };

  const handleToggleVpUserStatus = async (uname: string, currentActive: boolean) => {
    try {
      await adminUpdateVpUserStatus(uname, !currentActive);
      setVpUsers(prev => prev.map(u => u.username.toLowerCase() === uname.toLowerCase() ? { ...u, active: !currentActive } : u));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Ändern des Status');
    }
  };

  const handleSaveVpUserPin = async () => {
    if (!vpPinDialogUser || !vpPinDialogValue.trim()) return;
    try {
      await adminUpdateVpUserPin(vpPinDialogUser, vpPinDialogValue.trim(), vpPinDialogMustChange);
      alert(`PIN für VP-Nutzer "${vpPinDialogUser}" wurde aktualisiert.`);
      setVpPinDialogUser(null);
      setVpPinDialogValue('');
      const updated = await adminFetchVpUsers();
      setVpUsers(updated);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Ändern der PIN');
    }
  };

  const handleDeleteVpUser = async (uname: string) => {
    if (!confirm(`Möchtest du den VP-Only-Nutzer "${uname}" wirklich unwiderruflich löschen?`)) return;
    try {
      await adminDeleteVpUser(uname);
      setVpUsers(prev => prev.filter(u => u.username.toLowerCase() !== uname.toLowerCase()));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Löschen des VP-Nutzers');
    }
  };

  // --- CATEGORIES ---
  const handleAddCategory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCatName.trim()) return;
    const id = newCatName.trim().toUpperCase().replace(/\s+/g, '_');
    const newCat: EventCategory = { id, name: newCatName.trim(), color: newCatColor, sort_order: categories.length };
    try {
      const saved = await saveCategory(newCat);
      setCategories(prev => [...prev, saved]);
      setNewCatName('');
    } catch (err: any) {
      alert(err.message || 'Fehler beim Speichern der Kategorie');
    }
  };

  const handleUpdateCategory = async (id: string, name: string, color: string) => {
    try {
      const saved = await saveCategory({ id, name, color });
      setCategories(prev => prev.map(c => c.id === id ? saved : c));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Aktualisieren');
    }
  };

  const handleDeleteCategory = async (id: string) => {
    if (!confirm('Möchtest du diese Kategorie wirklich löschen?')) return;
    try {
      await deleteCategory(id);
      setCategories(prev => prev.filter(c => c.id !== id));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Löschen');
    }
  };

  // --- COURSES ---
  const handleAddCourse = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCourseName.trim() || !newCourseTeacher.trim()) return;
    const cleanName = newCourseName.trim();
    const id = cleanName.replace(/\s+/g, '_');
    const newCourse: Course = { id, name: cleanName, teacher: newCourseTeacher.trim(), type: newCourseType };
    try {
      await saveCourse(newCourse);
      const updated = await fetchCourses();
      setCourses(updated);
      setNewCourseName('');
      setNewCourseTeacher('');
    } catch (err: any) {
      alert(err.message || 'Fehler beim Speichern des Kurses');
    }
  };

  const handleUpdateCourse = async (id: string, name: string, teacher: string, type: 'LK' | 'GK' | 'AG') => {
    try {
      const updated: Course = { id, name, teacher, type };
      setCourses(prev => prev.map(c => c.id === id ? updated : c));
      await saveCourse(updated);
    } catch (err: any) {
      alert(err.message || 'Fehler beim Speichern');
    }
  };

  const handleDeleteCourse = async (id: string) => {
    if (!confirm('Möchtest du diesen Kurs wirklich löschen?')) return;
    try {
      await deleteCourse(id);
      setCourses(prev => prev.filter(c => c.id !== id));
    } catch (err: any) {
      alert(err.message || 'Fehler beim Löschen');
    }
  };

  const handleMoveWithinSection = async (courseId: string, direction: 'left' | 'right') => {
    const course = courses.find(c => c.id === courseId);
    if (!course) return;

    const sectionCourses = courses.filter(c => c.type === course.type);
    const secIdx = sectionCourses.findIndex(c => c.id === courseId);
    if (secIdx === -1) return;
    if (direction === 'left' && secIdx === 0) return;
    if (direction === 'right' && secIdx === sectionCourses.length - 1) return;

    const targetSecIdx = direction === 'left' ? secIdx - 1 : secIdx + 1;
    const targetCourse = sectionCourses[targetSecIdx];

    const mainIdx1 = courses.findIndex(c => c.id === course.id);
    const mainIdx2 = courses.findIndex(c => c.id === targetCourse.id);
    if (mainIdx1 === -1 || mainIdx2 === -1) return;

    const newCourses = [...courses];
    [newCourses[mainIdx1], newCourses[mainIdx2]] = [newCourses[mainIdx2], newCourses[mainIdx1]];
    setCourses(newCourses);

    try {
      await reorderCourses(newCourses.map(c => c.id));
    } catch (err) {
      console.error('Fehler beim Speichern der Reihenfolge', err);
    }
  };

  // Loading state
  if (loading) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${theme.bg}`}>
        <div className="flex items-center gap-3 text-lg font-semibold">
          <RefreshCw className="w-6 h-6 animate-spin text-blue-500" />
          <span>Admin-Bereich wird geladen...</span>
        </div>
      </div>
    );
  }

  // Not logged in -> Show Login Card
  if (!isAdmin && !currentUsername) {
    return (
      <div className={`min-h-screen flex items-center justify-center p-4 ${theme.bg}`}>
        <div className={`w-full max-w-md p-8 rounded-2xl shadow-xl border ${theme.card}`}>
          <div className="flex items-center justify-center w-14 h-14 bg-blue-600/10 text-blue-600 dark:text-blue-400 rounded-2xl mx-auto mb-6">
            <ShieldCheck className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold text-center mb-2">Admin-Anmeldung</h1>
          <p className={`text-sm text-center mb-6 ${theme.muted}`}>
            Bitte melde dich mit deinem Administrator-Konto an.
          </p>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className={`block text-xs font-bold uppercase tracking-wider mb-1.5 ${theme.muted}`}>
                Benutzername
              </label>
              <input
                type="text"
                value={loginUser}
                onChange={e => setLoginUser(e.target.value)}
                placeholder="z. B. admin"
                autoFocus
                className={`w-full px-4 py-3 rounded-xl border text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
              />
            </div>
            <div>
              <label className={`block text-xs font-bold uppercase tracking-wider mb-1.5 ${theme.muted}`}>
                Passwort / PIN (optional)
              </label>
              <input
                type="password"
                value={loginPin}
                onChange={e => setLoginPin(e.target.value)}
                placeholder="Passwort"
                className={`w-full px-4 py-3 rounded-xl border text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
              />
            </div>

            {loginError && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-sm rounded-xl">
                {loginError}
              </div>
            )}

            <button
              type="submit"
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl shadow-md transition-all cursor-pointer"
            >
              Im Admin-Bereich anmelden
            </button>
          </form>

          <div className="mt-6 pt-6 border-t border-gray-200 dark:border-gray-800 text-center">
            <Link to="/" className={`text-sm hover:underline ${theme.muted}`}>
              ← Zurück zur Startseite
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // Logged in as regular user (non-admin)
  if (!isAdmin) {
    return (
      <div className={`min-h-screen flex items-center justify-center p-4 ${theme.bg}`}>
        <div className={`w-full max-w-md p-8 rounded-2xl shadow-xl border text-center ${theme.card}`}>
          <div className="flex items-center justify-center w-14 h-14 bg-rose-500/10 text-rose-600 dark:text-rose-400 rounded-2xl mx-auto mb-6">
            <AlertTriangle className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold mb-2">Zugriff verweigert</h1>
          <p className={`text-sm mb-6 ${theme.muted}`}>
            Dein Konto <strong>{currentUsername}</strong> besitzt keine Administrator-Berechtigung.
          </p>
          <div className="flex flex-col gap-3">
            <Link
              to={`/${currentUsername}`}
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl shadow-md transition-all text-center"
            >
              Zum Kalender
            </Link>
            <button
              onClick={handleLogout}
              className={`w-full py-3 border font-semibold rounded-xl transition-all ${theme.hover} ${theme.border}`}
            >
              Abmelden
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Admin logged in, but needs PIN verification
  if (!isPinVerified) {
    return (
      <div className={`min-h-screen flex items-center justify-center p-4 ${theme.bg}`}>
        <div className={`w-full max-w-md p-8 rounded-2xl shadow-xl border ${theme.card}`}>
          <div className="flex items-center justify-center w-14 h-14 bg-amber-500/10 text-amber-600 dark:text-amber-400 rounded-2xl mx-auto mb-6">
            <Lock className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold text-center mb-2">Sicherheitsabfrage</h1>
          <p className={`text-sm text-center mb-6 ${theme.muted}`}>
            Hallo <strong>{currentUsername}</strong>! Bitte bestätige deine Admin-PIN, um das Admin-Panel zu entsperren.
          </p>

          <form onSubmit={handlePinChallenge} className="space-y-4">
            <div>
              <label className={`block text-xs font-bold uppercase tracking-wider mb-1.5 ${theme.muted}`}>
                Admin-PIN / Passwort
              </label>
              <input
                type="password"
                value={pinChallengeInput}
                onChange={e => setPinChallengeInput(e.target.value)}
                placeholder="Passwort / PIN"
                autoFocus
                className={`w-full px-4 py-3 rounded-xl border text-center font-mono text-xl tracking-widest focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
              />
            </div>

            {pinChallengeError && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-sm rounded-xl text-center">
                {pinChallengeError}
              </div>
            )}

            <button
              type="submit"
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl shadow-md transition-all cursor-pointer"
            >
              Admin-Panel entsperren
            </button>
          </form>

          <div className="mt-6 pt-6 border-t border-gray-200 dark:border-gray-800 flex justify-between text-xs">
            <Link to={`/${currentUsername}`} className={`hover:underline ${theme.muted}`}>
              ← Zum Kalender
            </Link>
            <button onClick={handleLogout} className={`hover:underline ${theme.muted}`}>
              Abmelden
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Filtered lists
  const filteredUsers = users.filter(u =>
    u.username.toLowerCase().includes(searchFilter.toLowerCase())
  );
  const filteredVpUsers = vpUsers.filter(u =>
    u.username.toLowerCase().includes(searchFilter.toLowerCase()) ||
    u.className.toLowerCase().includes(searchFilter.toLowerCase())
  );

  return (
    <div className={`min-h-screen flex flex-col ${theme.bg}`}>
      {/* Top Header */}
      <header className={`border-b ${theme.border} ${theme.card} sticky top-0 z-30 shadow-xs`}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 bg-blue-600 text-white rounded-xl shadow-xs">
              <ShieldCheck className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-lg font-extrabold tracking-tight">Admin-Zentrale</h1>
              <p className={`text-xs ${theme.muted}`}>Modern VpMobil & Kalender</p>
            </div>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <button
              onClick={() => setIsDark(!isDark)}
              title={isDark ? 'Heller Modus' : 'Dunkler Modus'}
              className={`p-2 rounded-xl border ${theme.border} ${theme.hover} ${theme.muted} transition-all`}
            >
              {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>

            <Link
              to={`/${currentUsername}`}
              className={`hidden sm:flex items-center gap-1.5 px-3 py-2 text-xs font-bold rounded-xl border ${theme.border} ${theme.hover} transition-all`}
            >
              <ExternalLink className="w-4 h-4" />
              Kalender öffnen
            </Link>

            <div className={`hidden md:flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs font-semibold ${theme.cardHeader}`}>
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span>Admin: <strong>{currentUsername}</strong></span>
            </div>

            <button
              onClick={handleLogout}
              title="Abmelden"
              className={`p-2 text-rose-500 rounded-xl border ${theme.border} hover:bg-rose-500/10 transition-all`}
            >
              <LogOut className="w-5 h-5" />
            </button>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-1 w-full flex flex-col md:flex-row gap-6">
        {/* Navigation Sidebar */}
        <aside className="w-full md:w-64 shrink-0 flex flex-col gap-2">
          {[
            { id: 'users', label: 'Kalender- & VP-Nutzer', icon: Users, count: users.length },
            { id: 'vp_users', label: 'VP-Only Nutzer', icon: UserPlus, count: vpUsers.length },
            { id: 'categories', label: 'Kategorien', icon: Palette, count: categories.length },
            { id: 'courses', label: 'Kurse & Fächer', icon: BookOpen, count: courses.length }
          ].map(tab => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id as any);
                  setSearchFilter('');
                }}
                className={`w-full flex items-center justify-between px-4 py-3.5 rounded-2xl text-sm font-bold transition-all cursor-pointer ${
                  isActive
                    ? 'bg-blue-600 text-white shadow-md'
                    : `${theme.card} border ${theme.border} ${theme.hover} ${theme.muted}`
                }`}
              >
                <div className="flex items-center gap-3">
                  <Icon className="w-5 h-5" />
                  <span>{tab.label}</span>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full ${isActive ? 'bg-white/20 text-white' : 'bg-gray-100 dark:bg-gray-800'}`}>
                  {tab.count}
                </span>
              </button>
            );
          })}

          <div className={`mt-6 p-4 rounded-2xl border ${theme.border} ${theme.cardHeader} text-xs space-y-2`}>
            <div className="font-bold flex items-center gap-1.5 text-blue-500">
              <ShieldCheck className="w-4 h-4" />
              Sicherheitshinweis
            </div>
            <p className={theme.muted}>
              Administrator-Konten haben Status <code>ADMIN</code> in der Datenbank. Admins können sich gegenseitig im Interface nicht bearbeiten oder löschen.
            </p>
          </div>
        </aside>

        {/* Tab Content */}
        <main className="flex-1 min-w-0">
          {/* TAB 1: SHARED / CALENDAR USERS */}
          {activeTab === 'users' && (
            <div className="space-y-6">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h2 className="text-xl font-black">Kalender- & Vertretungsplan-Benutzer</h2>
                  <p className={`text-sm ${theme.muted}`}>
                    Benutzer mit vollständigem Zugriff auf Kalender und Vertretungsplan.
                  </p>
                </div>
                <button
                  onClick={loadAllData}
                  className={`self-start sm:self-auto px-3 py-2 rounded-xl border text-xs font-bold flex items-center gap-2 ${theme.border} ${theme.hover}`}
                >
                  <RefreshCw className="w-3.5 h-3.5" /> Aktualisieren
                </button>
              </div>

              {/* Add User Form */}
              <form onSubmit={handleAddSharedUser} className={`p-4 rounded-2xl border ${theme.border} ${theme.card} space-y-3`}>
                <div className="text-xs font-bold uppercase tracking-wider text-blue-500">Neuen Benutzer anlegen</div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <input
                    type="text"
                    value={newUserName}
                    onChange={e => setNewUserName(e.target.value)}
                    placeholder="Benutzername"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
                  />
                  <input
                    type="password"
                    value={newUserPin}
                    onChange={e => setNewUserPin(e.target.value)}
                    placeholder="Passwort (optional)"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
                  />
                  <button
                    type="submit"
                    disabled={!newUserName.trim()}
                    className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-bold text-sm rounded-xl shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    <Plus className="w-4 h-4" /> Benutzer hinzufügen
                  </button>
                </div>
              </form>

              {/* Search & Bulk Bar */}
              <div className={`p-3 rounded-2xl border ${theme.border} ${theme.card} flex flex-col sm:flex-row items-center justify-between gap-3`}>
                <div className="relative w-full sm:w-72">
                  <Search className={`w-4 h-4 absolute left-3 top-3 ${theme.faint}`} />
                  <input
                    type="text"
                    value={searchFilter}
                    onChange={e => setSearchFilter(e.target.value)}
                    placeholder="Benutzer suchen..."
                    className={`w-full pl-9 pr-3 py-1.5 rounded-xl border text-xs focus:outline-none ${theme.input}`}
                  />
                </div>

                {selectedUsers.length > 0 && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs ${theme.muted}`}>{selectedUsers.length} ausgewählt:</span>
                    <button onClick={() => handleBulkUpdateStatus('ACTIVE')} className="px-2 py-1 text-xs font-bold bg-emerald-500/10 text-emerald-600 rounded-lg hover:bg-emerald-500/20">Aktiv</button>
                    <button onClick={() => handleBulkUpdateStatus('READ_ONLY')} className="px-2 py-1 text-xs font-bold bg-amber-500/10 text-amber-600 rounded-lg hover:bg-amber-500/20">Nur Lesen</button>
                    <button onClick={() => handleBulkUpdateStatus('BLOCKED')} className="px-2 py-1 text-xs font-bold bg-rose-500/10 text-rose-600 rounded-lg hover:bg-rose-500/20">Sperren</button>
                  </div>
                )}
              </div>

              {/* Users Table */}
              <div className={`rounded-2xl border overflow-hidden ${theme.border} ${theme.card}`}>
                <div className={`px-4 py-3 border-b flex items-center justify-between ${theme.border} ${theme.cardHeader}`}>
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={users.filter(u => !u.isAdmin && u.status !== 'ADMIN').length > 0 && selectedUsers.length === users.filter(u => !u.isAdmin && u.status !== 'ADMIN').length}
                      onChange={handleSelectAllUsers}
                      className="w-4 h-4 rounded text-blue-600"
                    />
                    <span className="text-xs font-bold uppercase tracking-wider">Benutzer ({filteredUsers.length})</span>
                  </div>
                </div>

                <div className="divide-y divide-gray-100 dark:divide-gray-800">
                  {filteredUsers.length === 0 ? (
                    <div className={`p-8 text-center text-sm ${theme.muted}`}>
                      Keine Benutzer gefunden.
                    </div>
                  ) : (
                    filteredUsers.map(u => {
                      const isUserAdmin = u.isAdmin || u.status === 'ADMIN';
                      return (
                        <div key={u.username} className={`p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${theme.hover}`}>
                          <div className="flex items-center gap-3">
                            <input
                              type="checkbox"
                              checked={selectedUsers.includes(u.username)}
                              onChange={() => handleSelectUser(u.username)}
                              disabled={isUserAdmin}
                              className="w-4 h-4 rounded text-blue-600 disabled:opacity-20"
                            />
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-sm">{u.username}</span>
                                {isUserAdmin && (
                                  <span className="px-2 py-0.5 text-[10px] font-black uppercase rounded-full bg-purple-500/10 text-purple-600 border border-purple-500/20">
                                    Admin
                                  </span>
                                )}
                              </div>
                              <div className={`text-xs font-semibold mt-0.5 ${
                                isUserAdmin ? 'text-purple-500' :
                                u.status === 'ACTIVE' ? 'text-emerald-500' :
                                u.status === 'READ_ONLY' ? 'text-amber-500' : 'text-rose-500'
                              }`}>
                                {isUserAdmin ? 'Administrator' : u.status === 'ACTIVE' ? 'Aktiviert' : u.status === 'READ_ONLY' ? 'Nur Lesezugriff' : 'Gesperrt'}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 self-end sm:self-auto flex-wrap">
                            {!isUserAdmin ? (
                              <>
                                <select
                                  value={u.status || 'ACTIVE'}
                                  onChange={(e) => handleUpdateUserStatus(u.username, e.target.value as any)}
                                  className={`text-xs px-2.5 py-1.5 rounded-xl border focus:outline-none ${theme.border} ${theme.input}`}
                                >
                                  <option value="ACTIVE">Aktiv</option>
                                  <option value="READ_ONLY">Nur Lesen</option>
                                  <option value="BLOCKED">Sperren</option>
                                </select>

                                <button
                                  onClick={() => {
                                    setPinDialogUser(u.username);
                                    setPinDialogValue('');
                                  }}
                                  title="Passwort ändern"
                                  className="px-2.5 py-1.5 text-xs font-bold text-blue-600 bg-blue-500/10 hover:bg-blue-500/20 rounded-xl transition-all flex items-center gap-1 cursor-pointer"
                                >
                                  <KeyRound className="w-3.5 h-3.5" /> Passwort setzen
                                </button>

                                <button
                                  onClick={() => handleResetUserPin(u.username)}
                                  title="Passwort entfernen"
                                  className="p-1.5 text-amber-500 hover:bg-amber-500/10 rounded-xl transition-all cursor-pointer"
                                >
                                  <Unlock className="w-4 h-4" />
                                </button>

                                <button
                                  onClick={() => handleDeleteSharedUser(u.username)}
                                  title="Benutzer löschen"
                                  className="p-1.5 text-rose-500 hover:bg-rose-500/10 rounded-xl transition-all cursor-pointer"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </>
                            ) : (
                              <span className={`text-xs italic ${theme.faint}`}>
                                (Admin - unveränderlich)
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: VP-ONLY USERS */}
          {activeTab === 'vp_users' && (
            <div className="space-y-6">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h2 className="text-xl font-black">Vertretungsplan-Only-Benutzer</h2>
                  <p className={`text-sm ${theme.muted}`}>
                    Benutzerkonten mit reinem Zugriff auf den Vertretungsplan (ohne Kalender).
                  </p>
                </div>
                <button
                  onClick={loadAllData}
                  className={`self-start sm:self-auto px-3 py-2 rounded-xl border text-xs font-bold flex items-center gap-2 ${theme.border} ${theme.hover}`}
                >
                  <RefreshCw className="w-3.5 h-3.5" /> Aktualisieren
                </button>
              </div>

              {/* Add VP User Form */}
              <form onSubmit={handleAddVpUser} className={`p-4 rounded-2xl border ${theme.border} ${theme.card} space-y-3`}>
                <div className="text-xs font-bold uppercase tracking-wider text-purple-500">Neuen VP-Nutzer erstellen</div>
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                  <input
                    type="text"
                    value={newVpUsername}
                    onChange={e => setNewVpUsername(e.target.value)}
                    placeholder="Benutzername"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 ${theme.input}`}
                  />
                  <input
                    type="password"
                    maxLength={4}
                    value={newVpPin}
                    onChange={e => setNewVpPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                    placeholder="4-stellige Start-PIN"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm font-mono text-center tracking-widest focus:outline-none focus:ring-2 focus:ring-purple-500 ${theme.input}`}
                  />
                  <input
                    type="text"
                    value={newVpClass}
                    onChange={e => setNewVpClass(e.target.value)}
                    placeholder="Klasse (z. B. 11)"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 ${theme.input}`}
                  />
                  <button
                    type="submit"
                    disabled={!newVpUsername.trim() || newVpPin.length !== 4}
                    className="px-4 py-2.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-bold text-sm rounded-xl shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    <UserPlus className="w-4 h-4" /> VP-Nutzer anlegen
                  </button>
                </div>
              </form>

              {/* Search Bar */}
              <div className={`p-3 rounded-2xl border ${theme.border} ${theme.card} flex items-center justify-between`}>
                <div className="relative w-full sm:w-72">
                  <Search className={`w-4 h-4 absolute left-3 top-3 ${theme.faint}`} />
                  <input
                    type="text"
                    value={searchFilter}
                    onChange={e => setSearchFilter(e.target.value)}
                    placeholder="VP-Nutzer / Klasse suchen..."
                    className={`w-full pl-9 pr-3 py-1.5 rounded-xl border text-xs focus:outline-none ${theme.input}`}
                  />
                </div>
              </div>

              {/* VP Users Table */}
              <div className={`rounded-2xl border overflow-hidden ${theme.border} ${theme.card}`}>
                <div className={`px-4 py-3 border-b flex items-center justify-between ${theme.border} ${theme.cardHeader}`}>
                  <span className="text-xs font-bold uppercase tracking-wider">VP-Nutzer ({filteredVpUsers.length})</span>
                </div>

                <div className="divide-y divide-gray-100 dark:divide-gray-800">
                  {filteredVpUsers.length === 0 ? (
                    <div className={`p-8 text-center text-sm ${theme.muted}`}>
                      Keine reinen VP-Nutzer vorhanden.
                    </div>
                  ) : (
                    filteredVpUsers.map(u => (
                      <div key={u.username} className={`p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${theme.hover}`}>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-sm">{u.username}</span>
                            <span className="px-2 py-0.5 text-[10px] font-bold rounded-md bg-purple-500/10 text-purple-600 border border-purple-500/20">
                              Klasse {u.className}
                            </span>
                            {u.mustChangePin && (
                              <span className="px-2 py-0.5 text-[10px] font-semibold rounded-md bg-amber-500/10 text-amber-600">
                                PIN muss geändert werden
                              </span>
                            )}
                          </div>
                          <div className={`text-xs mt-1 flex items-center gap-3 ${theme.muted}`}>
                            <span className={u.active ? 'text-emerald-500 font-semibold' : 'text-rose-500 font-semibold'}>
                              {u.active ? '● Aktiv' : '○ Inaktiv'}
                            </span>
                            <span>Erstellt von: <strong>{u.createdBy || 'Admin'}</strong></span>
                            {u.createdAt && <span>am {new Date(u.createdAt).toLocaleDateString('de-DE')}</span>}
                          </div>
                        </div>

                        <div className="flex items-center gap-2 self-end sm:self-auto flex-wrap">
                          <button
                            onClick={() => handleToggleVpUserStatus(u.username, u.active)}
                            className={`px-2.5 py-1.5 text-xs font-bold rounded-xl border transition-all cursor-pointer ${
                              u.active ? 'text-rose-500 border-rose-500/20 hover:bg-rose-500/10' : 'text-emerald-500 border-emerald-500/20 hover:bg-emerald-500/10'
                            }`}
                          >
                            {u.active ? 'Deaktivieren' : 'Aktivieren'}
                          </button>

                          <button
                            onClick={() => {
                              setVpPinDialogUser(u.username);
                              setVpPinDialogValue('');
                              setVpPinDialogMustChange(true);
                            }}
                            title="PIN ändern / zurücksetzen"
                            className="px-2.5 py-1.5 text-xs font-bold text-purple-600 bg-purple-500/10 hover:bg-purple-500/20 rounded-xl transition-all flex items-center gap-1 cursor-pointer"
                          >
                            <KeyRound className="w-3.5 h-3.5" /> PIN ändern
                          </button>

                          <button
                            onClick={() => handleDeleteVpUser(u.username)}
                            title="VP-Nutzer löschen"
                            className="p-1.5 text-rose-500 hover:bg-rose-500/10 rounded-xl transition-all cursor-pointer"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: CATEGORIES */}
          {activeTab === 'categories' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-xl font-black">Kategorienverwaltung</h2>
                <p className={`text-sm ${theme.muted}`}>
                  Kategorien für Termine, Klausuren, Hausaufgaben und Ferien verwalten.
                </p>
              </div>

              <form onSubmit={handleAddCategory} className={`p-4 rounded-2xl border ${theme.border} ${theme.card} space-y-3`}>
                <div className="text-xs font-bold uppercase tracking-wider text-emerald-500">Neue Kategorie erstellen</div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <input
                    type="text"
                    value={newCatName}
                    onChange={e => setNewCatName(e.target.value)}
                    placeholder="Kategoriename (z.B. Klausur)"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 ${theme.input}`}
                  />
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={newCatColor}
                      onChange={e => setNewCatColor(e.target.value)}
                      className="w-10 h-10 p-1 rounded-xl border border-gray-300 dark:border-gray-700 bg-transparent cursor-pointer"
                    />
                    <input
                      type="text"
                      value={newCatColor}
                      onChange={e => setNewCatColor(e.target.value)}
                      className={`flex-1 px-3.5 py-2.5 rounded-xl border text-sm font-mono uppercase focus:outline-none ${theme.input}`}
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!newCatName.trim()}
                    className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-bold text-sm rounded-xl shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    <Plus className="w-4 h-4" /> Kategorie hinzufügen
                  </button>
                </div>
              </form>

              <div className={`rounded-2xl border overflow-hidden ${theme.border} ${theme.card}`}>
                <div className={`px-4 py-3 border-b ${theme.border} ${theme.cardHeader}`}>
                  <span className="text-xs font-bold uppercase tracking-wider">Vorhandene Kategorien</span>
                </div>
                <div className="divide-y divide-gray-100 dark:divide-gray-800">
                  {categories.map(cat => (
                    <div key={cat.id} className={`p-4 flex items-center justify-between gap-3 ${theme.hover}`}>
                      <div className="flex items-center gap-3">
                        <div className="w-4 h-4 rounded-full" style={{ backgroundColor: cat.color }} />
                        <span className="font-bold text-sm">{cat.name}</span>
                        <span className={`text-xs font-mono ${theme.faint}`}>({cat.id})</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <input
                          type="color"
                          value={cat.color}
                          onChange={e => handleUpdateCategory(cat.id, cat.name, e.target.value)}
                          className="w-8 h-8 p-0.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent cursor-pointer"
                        />
                        <button
                          onClick={() => handleDeleteCategory(cat.id)}
                          className="p-1.5 text-rose-500 hover:bg-rose-500/10 rounded-xl transition-all cursor-pointer"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: COURSES */}
          {activeTab === 'courses' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-xl font-black">Kurs- & Fächerverwaltung</h2>
                <p className={`text-sm ${theme.muted}`}>
                  Kurse für Leistungskurse (LK), Grundkurse (GK) und Arbeitsgemeinschaften (AG) verwalten.
                </p>
              </div>

              <form onSubmit={handleAddCourse} className={`p-4 rounded-2xl border ${theme.border} ${theme.card} space-y-3`}>
                <div className="text-xs font-bold uppercase tracking-wider text-blue-500">Neuen Kurs anlegen</div>
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                  <input
                    type="text"
                    value={newCourseName}
                    onChange={e => setNewCourseName(e.target.value)}
                    placeholder="Kursname (z. B. M1, DE2)"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
                  />
                  <input
                    type="text"
                    value={newCourseTeacher}
                    onChange={e => setNewCourseTeacher(e.target.value)}
                    placeholder="Lehrerkürzel (z. B. MÜ)"
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${theme.input}`}
                  />
                  <select
                    value={newCourseType}
                    onChange={e => setNewCourseType(e.target.value as any)}
                    className={`px-3.5 py-2.5 rounded-xl border text-sm focus:outline-none ${theme.input}`}
                  >
                    <option value="LK">Leistungskurs (LK)</option>
                    <option value="GK">Grundkurs (GK)</option>
                    <option value="AG">Arbeitsgemeinschaft (AG)</option>
                  </select>
                  <button
                    type="submit"
                    disabled={!newCourseName.trim() || !newCourseTeacher.trim()}
                    className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-bold text-sm rounded-xl shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    <Plus className="w-4 h-4" /> Kurs hinzufügen
                  </button>
                </div>
              </form>

              {/* Course Sections */}
              {(['LK', 'GK', 'AG'] as const).map(sectionType => {
                const secCourses = courses.filter(c => c.type === sectionType);
                return (
                  <div key={sectionType} className={`rounded-2xl border overflow-hidden ${theme.border} ${theme.card}`}>
                    <div className={`px-4 py-3 border-b flex items-center justify-between ${theme.border} ${theme.cardHeader}`}>
                      <span className="text-xs font-bold uppercase tracking-wider">
                        {sectionType === 'LK' ? 'Leistungskurse (LK)' : sectionType === 'GK' ? 'Grundkurse (GK)' : 'Arbeitsgemeinschaften (AG)'} ({secCourses.length})
                      </span>
                    </div>

                    <div className="divide-y divide-gray-100 dark:divide-gray-800">
                      {secCourses.length === 0 ? (
                        <div className={`p-4 text-center text-xs ${theme.muted}`}>
                          Keine Kurse vorhanden.
                        </div>
                      ) : (
                        secCourses.map((c, idx) => (
                          <div key={c.id} className={`p-3.5 flex items-center justify-between gap-3 ${theme.hover}`}>
                            <div className="flex items-center gap-3">
                              <div className="w-7 h-7 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400 font-black text-xs flex items-center justify-center">
                                {idx + 1}
                              </div>
                              <div>
                                <span className="font-bold text-sm">{c.name}</span>
                                <span className={`text-xs ml-2 ${theme.muted}`}>({c.teacher})</span>
                              </div>
                            </div>

                            <div className="flex items-center gap-1.5">
                              <button
                                onClick={() => handleMoveWithinSection(c.id, 'left')}
                                disabled={idx === 0}
                                title="Nach oben / links verschieben"
                                className={`p-1.5 rounded-lg border text-xs disabled:opacity-20 ${theme.border} ${theme.hover}`}
                              >
                                <ArrowLeft className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => handleMoveWithinSection(c.id, 'right')}
                                disabled={idx === secCourses.length - 1}
                                title="Nach unten / rechts verschieben"
                                className={`p-1.5 rounded-lg border text-xs disabled:opacity-20 ${theme.border} ${theme.hover}`}
                              >
                                <ArrowRight className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => handleDeleteCourse(c.id)}
                                title="Kurs löschen"
                                className="p-1.5 text-rose-500 hover:bg-rose-500/10 rounded-lg transition-all cursor-pointer"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </main>
      </div>

      {/* Shared User PIN Set Modal */}
      {pinDialogUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className={`w-full max-w-sm p-6 rounded-2xl shadow-xl border ${theme.card}`}>
            <h3 className="text-lg font-bold mb-2">Passwort für {pinDialogUser} setzen</h3>
            <p className={`text-xs mb-4 ${theme.muted}`}>
              Gib ein neues Passwort ein. Leerlassen entfernt die Passwort-Pflicht.
            </p>
            <input
              type="password"
              value={pinDialogValue}
              onChange={e => setPinDialogValue(e.target.value)}
              placeholder="Neues Passwort"
              autoFocus
              className={`w-full px-4 py-2.5 rounded-xl border text-sm mb-4 focus:outline-none ${theme.input}`}
            />
            <div className="flex gap-2">
              <button
                onClick={() => setPinDialogUser(null)}
                className={`flex-1 py-2.5 rounded-xl border font-bold text-xs ${theme.border} ${theme.hover}`}
              >
                Abbrechen
              </button>
              <button
                onClick={handleSaveUserPin}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs rounded-xl shadow-xs cursor-pointer"
              >
                Speichern
              </button>
            </div>
          </div>
        </div>
      )}

      {/* VP User PIN Change Modal */}
      {vpPinDialogUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className={`w-full max-w-sm p-6 rounded-2xl shadow-xl border ${theme.card}`}>
            <h3 className="text-lg font-bold mb-2">PIN für VP-Nutzer {vpPinDialogUser} ändern</h3>
            <p className={`text-xs mb-4 ${theme.muted}`}>
              Gib eine neue 4-stellige PIN für das VP-Konto ein.
            </p>
            <input
              type="password"
              maxLength={4}
              value={vpPinDialogValue}
              onChange={e => setVpPinDialogValue(e.target.value.replace(/\D/g, '').slice(0, 4))}
              placeholder="4-stellige PIN"
              autoFocus
              className={`w-full px-4 py-2.5 rounded-xl border text-center font-mono text-xl tracking-widest mb-4 focus:outline-none ${theme.input}`}
            />
            <label className="flex items-center gap-2 text-xs mb-4 cursor-pointer">
              <input
                type="checkbox"
                checked={vpPinDialogMustChange}
                onChange={e => setVpPinDialogMustChange(e.target.checked)}
                className="w-4 h-4 rounded text-purple-600"
              />
              <span>Nutzer muss PIN bei nächstem Login ändern</span>
            </label>
            <div className="flex gap-2">
              <button
                onClick={() => setVpPinDialogUser(null)}
                className={`flex-1 py-2.5 rounded-xl border font-bold text-xs ${theme.border} ${theme.hover}`}
              >
                Abbrechen
              </button>
              <button
                onClick={handleSaveVpUserPin}
                disabled={vpPinDialogValue.length !== 4}
                className="flex-1 py-2.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-bold text-xs rounded-xl shadow-xs cursor-pointer"
              >
                PIN setzen
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
