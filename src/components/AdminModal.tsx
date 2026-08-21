import React, { useState, useEffect, FormEvent } from 'react';
import { adminFetchUsers, adminUpdateUserStatus, adminResetUserPin, adminAddUser, adminDeleteUser, fetchCategories, saveCategory, deleteCategory, reorderCategories, fetchCourses, saveCourse, deleteCourse, reorderCourses } from '../lib/api';
import { User, EventCategory, UserPreferences, Course } from '../types';
import { X, ShieldCheck, Search, ShieldAlert, KeyRound, Check, Trash2, Plus, GripVertical, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Layers } from 'lucide-react';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  username: string;
  preferences: UserPreferences;
}

export default function AdminModal({ isOpen, onClose, username, preferences }: Props) {
  const [activeTab, setActiveTab] = useState<'users' | 'categories' | 'courses'>('users');
  const [users, setUsers] = useState<{ username: string; status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED' | 'ADMIN', isAdmin?: boolean }[]>([]);
  const [categories, setCategories] = useState<EventCategory[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  
  // Pin protection
  const [pinInput, setPinInput] = useState('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [pinError, setPinError] = useState('');

  const isDark = preferences.darkMode;
  const theme = {
    bgModal: isDark ? 'bg-[#1a1a1a]' : 'bg-white',
    bgSidebar: isDark ? 'bg-[#121212]' : 'bg-[#fcfcfc]',
    bgHover: isDark ? 'hover:bg-[#252525]' : 'hover:bg-[#f3f4f6]',
    bgInput: isDark ? 'bg-[#222]' : 'bg-[#f9fafb]',
    border: isDark ? 'border-[#333]' : 'border-[#e5e7eb]',
    borderInput: isDark ? 'border-[#444]' : 'border-[#d1d5db]',
    textMain: isDark ? 'text-[#f3f4f6]' : 'text-[#1f2937]',
    textMuted: isDark ? 'text-[#9ca3af]' : 'text-[#4b5563]',
    textFaint: isDark ? 'text-[#6b7280]' : 'text-[#6b7280]',
    overlay: isDark ? 'bg-black/70' : 'bg-black/50',
    accent: preferences.accentColor
  };

  useEffect(() => {
    if (isOpen && isAuthenticated) {
      loadData();
    }
  }, [isOpen, isAuthenticated]);

  const loadData = async () => {
    try {
      const fetchedUsers = await adminFetchUsers();
      setUsers(fetchedUsers);
      const fetchedCats = await fetchCategories();
      setCategories(fetchedCats);
      const fetchedCourses = await fetchCourses();
      setCourses(fetchedCourses);
    } catch (err) {
      console.error(err);
    }
  };

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch(`/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, pin: pinInput })
      });
      if (res.ok) {
        setIsAuthenticated(true);
      } else {
        setPinError('Falsches Passwort.');
      }
    } catch (err) {
      setPinError('Fehler bei der Anmeldung.');
    }
  };

  // User management
  const [selectedUsers, setSelectedUsers] = useState<string[]>([]);
  const [newUserName, setNewUserName] = useState('');
  const [newUserPin, setNewUserPin] = useState('');
  
  const handleAddUser = async () => {
    if (!newUserName.trim()) return;
    try {
      await adminAddUser(newUserName.trim(), newUserPin.trim() || undefined);
      const fetchedUsers = await adminFetchUsers();
      setUsers(fetchedUsers);
      setNewUserName('');
      setNewUserPin('');
    } catch (e: any) {
      alert(e.message || 'Fehler beim Erstellen');
    }
  };

  const handleDeleteUser = async (uname: string) => {
    if (!confirm(`Benutzer ${uname} wirklich löschen?`)) return;
    try {
      await adminDeleteUser(uname);
      setUsers(prev => prev.filter(u => u.username !== uname));
    } catch (e: any) {
      alert(e.message || 'Fehler beim Löschen');
    }
  };

  const handleSelectUser = (uname: string) => {
    setSelectedUsers(prev => 
      prev.includes(uname) ? prev.filter(u => u !== uname) : [...prev, uname]
    );
  };
  
  const handleSelectAllUsers = () => {
    const mutableUsers = users.filter(u => !u.isAdmin && u.status !== 'ADMIN' && u.username.toLowerCase() !== 'gustavd').map(u => u.username);
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
    } catch (err) {
      alert('Fehler beim Speichern');
    }
  };

  const handleUpdateStatus = async (uname: string, status: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED') => {
    try {
      await adminUpdateUserStatus(uname, status);
      setUsers(prev => prev.map(u => u.username === uname ? { ...u, status } : u));
    } catch (err) {
      alert('Fehler beim Speichern');
    }
  };

  const handleResetPin = async (uname: string) => {
    if (!confirm(`PIN von ${uname} wirklich zurücksetzen?`)) return;
    try {
      await adminResetUserPin(uname);
      alert(`PIN für ${uname} wurde gelöscht.`);
    } catch (err) {
      alert('Fehler beim Zurücksetzen.');
    }
  };

  // Category management
  const [newCatName, setNewCatName] = useState('');
  const [newCatColor, setNewCatColor] = useState('#e91e63');

  const handleAddCategory = async () => {
    if (!newCatName.trim()) return;
    const id = newCatName.trim().toUpperCase().replace(/\s+/g, '_');
    const newCat = { id, name: newCatName.trim(), color: newCatColor, sort_order: categories.length };
    try {
      const saved = await saveCategory(newCat);
      setCategories(prev => [...prev, saved]);
      setNewCatName('');
    } catch (e) {
      alert('Fehler beim Speichern');
    }
  };

  const handleUpdateCategory = async (id: string, name: string, color: string) => {
    try {
      const saved = await saveCategory({ id, name, color });
      setCategories(prev => prev.map(c => c.id === id ? saved : c));
    } catch (e) {
      alert('Fehler beim Speichern');
    }
  };

  const handleDeleteCategory = async (id: string) => {
    if (!confirm('Kategorie wirklich löschen?')) return;
    try {
      await deleteCategory(id);
      setCategories(prev => prev.filter(c => c.id !== id));
    } catch (e) {
      alert('Fehler beim Löschen');
    }
  };

  // Course management
  const [newCourseName, setNewCourseName] = useState('');
  const [newCourseTeacher, setNewCourseTeacher] = useState('');
  const [newCourseType, setNewCourseType] = useState<'LK' | 'GK' | 'AG'>('GK');

  // Drag and Drop state
  const [draggedCourseId, setDraggedCourseId] = useState<string | null>(null);
  const [dragOverCourseId, setDragOverCourseId] = useState<string | null>(null);
  const [dragOverSection, setDragOverSection] = useState<'LK' | 'GK' | 'AG' | null>(null);

  const lks = courses.filter(c => c.type === 'LK');
  const gks = courses.filter(c => c.type === 'GK');
  const ags = courses.filter(c => c.type === 'AG');

  const handleAddCourse = async () => {
    if (!newCourseName.trim() || !newCourseTeacher.trim()) return;
    const cleanName = newCourseName.trim();
    const id = cleanName.replace(/\s+/g, '_');
    const newCourse: Course = { id, name: cleanName, teacher: newCourseTeacher.trim(), type: newCourseType };
    try {
      await saveCourse(newCourse);
      const fetchedCourses = await fetchCourses();
      setCourses(fetchedCourses);
      setNewCourseName('');
      setNewCourseTeacher('');
    } catch (e) {
      alert('Fehler beim Speichern');
    }
  };

  const handleUpdateCourse = async (id: string, name: string, teacher: string, type: 'LK' | 'GK' | 'AG') => {
    try {
      const updated: Course = { id, name, teacher, type };
      setCourses(prev => prev.map(c => c.id === id ? updated : c));
      await saveCourse(updated);
    } catch (e) {
      alert('Fehler beim Speichern');
    }
  };

  const handleDeleteCourse = async (id: string) => {
    if (!confirm('Kurs wirklich löschen?')) return;
    try {
      await deleteCourse(id);
      setCourses(prev => prev.filter(c => c.id !== id));
    } catch (e) {
      alert('Fehler beim Löschen');
    }
  };

  // Drag & Drop handlers
  const handleDragStart = (e: React.DragEvent, id: string) => {
    e.dataTransfer.setData('text/plain', id);
    e.dataTransfer.effectAllowed = 'move';
    setDraggedCourseId(id);
  };

  const handleDragEnd = () => {
    setDraggedCourseId(null);
    setDragOverCourseId(null);
    setDragOverSection(null);
  };

  const handleDragOverCard = (e: React.DragEvent, targetId: string, section: 'LK' | 'GK' | 'AG') => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'move';
    if (dragOverCourseId !== targetId) {
      setDragOverCourseId(targetId);
    }
    if (dragOverSection !== section) {
      setDragOverSection(section);
    }
  };

  const handleDragOverSection = (e: React.DragEvent, section: 'LK' | 'GK' | 'AG') => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (dragOverSection !== section) {
      setDragOverSection(section);
    }
  };

  const handleDropOnCard = async (e: React.DragEvent, targetCourse: Course) => {
    e.preventDefault();
    e.stopPropagation();
    const sourceId = e.dataTransfer.getData('text/plain') || draggedCourseId;
    if (!sourceId || sourceId === targetCourse.id) {
      handleDragEnd();
      return;
    }

    const sourceCourse = courses.find(c => c.id === sourceId);
    if (!sourceCourse) {
      handleDragEnd();
      return;
    }

    const updatedSource = { ...sourceCourse, type: targetCourse.type };
    const otherCourses = courses.filter(c => c.id !== sourceId);
    const targetIdx = otherCourses.findIndex(c => c.id === targetCourse.id);
    const newCourses = [...otherCourses];
    if (targetIdx !== -1) {
      newCourses.splice(targetIdx, 0, updatedSource);
    } else {
      newCourses.push(updatedSource);
    }

    setCourses(newCourses);
    handleDragEnd();

    try {
      if (sourceCourse.type !== targetCourse.type) {
        await saveCourse(updatedSource);
      }
      await reorderCourses(newCourses.map(c => c.id));
    } catch (err) {
      console.error('Failed to save reordered courses', err);
    }
  };

  const handleDropOnSection = async (e: React.DragEvent, targetSection: 'LK' | 'GK' | 'AG') => {
    e.preventDefault();
    const sourceId = e.dataTransfer.getData('text/plain') || draggedCourseId;
    if (!sourceId) {
      handleDragEnd();
      return;
    }

    const sourceCourse = courses.find(c => c.id === sourceId);
    if (!sourceCourse) {
      handleDragEnd();
      return;
    }

    if (sourceCourse.type === targetSection) {
      handleDragEnd();
      return;
    }

    const updatedSource = { ...sourceCourse, type: targetSection };
    const otherCourses = courses.filter(c => c.id !== sourceId);
    
    // Find last index of targetSection in otherCourses
    let lastSectionIdx = -1;
    for (let i = otherCourses.length - 1; i >= 0; i--) {
      if (otherCourses[i].type === targetSection) {
        lastSectionIdx = i;
        break;
      }
    }

    const newCourses = [...otherCourses];
    if (lastSectionIdx !== -1) {
      newCourses.splice(lastSectionIdx + 1, 0, updatedSource);
    } else {
      newCourses.push(updatedSource);
    }

    setCourses(newCourses);
    handleDragEnd();

    try {
      await saveCourse(updatedSource);
      await reorderCourses(newCourses.map(c => c.id));
    } catch (err) {
      console.error('Failed to save reordered courses', err);
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
      console.error('Failed to save order', err);
    }
  };

  const handleChangeCourseType = async (courseId: string, newType: 'LK' | 'GK' | 'AG') => {
    const course = courses.find(c => c.id === courseId);
    if (!course || course.type === newType) return;

    const updated = { ...course, type: newType };
    const otherCourses = courses.filter(c => c.id !== courseId);
    
    let insertIdx = otherCourses.length;
    for (let i = otherCourses.length - 1; i >= 0; i--) {
      if (otherCourses[i].type === newType) {
        insertIdx = i + 1;
        break;
      }
    }
    const newCourses = [...otherCourses];
    newCourses.splice(insertIdx, 0, updated);

    setCourses(newCourses);
    try {
      await saveCourse(updated);
      await reorderCourses(newCourses.map(c => c.id));
    } catch (err) {
      console.error('Failed to save course', err);
    }
  };

  if (!isOpen) return null;

  if (!isAuthenticated) {
    return (
      <div className={`fixed inset-0 z-[100] flex items-center justify-center ${theme.overlay} backdrop-blur-sm p-4`}>
        <div className={`w-full max-w-sm ${theme.bgModal} rounded-2xl shadow-xl border ${theme.border} overflow-hidden animate-in zoom-in-95 duration-200 p-6`}>
          <div className="flex justify-between items-center mb-6">
            <h2 className={`text-xl font-bold ${theme.textMain}`}>
              Admin-Bereich
            </h2>
            <button onClick={onClose} className={`${theme.textMuted} ${theme.bgHover} p-1 rounded-lg`}>
              <X className="w-5 h-5" />
            </button>
          </div>
          <form onSubmit={handleAuth} className="space-y-4">
            <div>
              <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Bitte Passwort eingeben</label>
              <input
                type="password"
                value={pinInput}
                onChange={e => setPinInput(e.target.value)}
                className={`w-full px-4 py-2.5 ${theme.bgInput} border ${theme.borderInput} ${theme.textMain} rounded-xl text-center font-mono text-xl tracking-widest focus:outline-none`}
              />
            </div>
            {pinError && <p className="text-sm text-rose-500 font-medium">{pinError}</p>}
            <button type="submit" className="w-full py-2.5 rounded-xl font-bold text-white shadow-xs" style={{ backgroundColor: theme.accent }}>
              Verifizieren
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className={`fixed inset-0 z-[100] flex items-center justify-center ${theme.overlay} backdrop-blur-sm p-4 sm:p-6`}>
      <div className={`w-full max-w-4xl max-h-[90vh] ${theme.bgModal} rounded-2xl shadow-2xl border ${theme.border} flex flex-col overflow-hidden animate-in zoom-in-95 duration-200`}>
        
        <div className={`flex items-center justify-between px-6 py-4 border-b ${theme.border} ${theme.bgSidebar}`}>
          <h2 className={`text-xl font-bold ${theme.textMain} flex items-center gap-2`}>
            Admin-Einstellungen
          </h2>
          <button onClick={onClose} className={`p-2 rounded-xl ${theme.bgHover} ${theme.textMuted} transition-colors`}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar */}
          <div className={`w-48 sm:w-56 shrink-0 border-r ${theme.border} ${theme.bgSidebar} p-4 flex flex-col gap-2`}>
            {[
              { id: 'users', label: 'Benutzer' },
              { id: 'categories', label: 'Kategorien' },
              { id: 'courses', label: 'Kurse' }
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`w-full text-left px-4 py-3 text-sm font-semibold rounded-xl transition-all ${
                  activeTab === tab.id 
                    ? `text-white shadow-xs` 
                    : `${theme.textMuted} ${theme.bgHover}`
                }`}
                style={{ backgroundColor: activeTab === tab.id ? theme.accent : undefined }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className={`flex-1 p-6 overflow-y-auto ${theme.bgModal}`}>
            {activeTab === 'users' && (
              <div className="space-y-6">
                <div>
                  <h3 className={`font-bold text-xl ${theme.textMain} mb-1`}>Benutzerverwaltung</h3>
                  <p className={`text-sm ${theme.textFaint}`}>Setze PINs zurück oder sperre Benutzer (Lesezugriff oder komplett blockiert).</p>
                </div>
                
                <div className={`flex items-center gap-3 p-4 border ${theme.border} rounded-xl ${theme.bgInput}`}>
                  <input
                    type="text"
                    value={newUserName}
                    onChange={e => setNewUserName(e.target.value)}
                    placeholder="Benutzername"
                    className={`flex-1 px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-lg text-sm ${theme.textMain} focus:outline-none`}
                  />
                  <input
                    type="password"
                    value={newUserPin}
                    onChange={e => setNewUserPin(e.target.value)}
                    placeholder="Passwort (optional)"
                    className={`flex-1 px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-lg text-sm ${theme.textMain} focus:outline-none`}
                  />
                  <button
                    onClick={handleAddUser}
                    disabled={!newUserName.trim()}
                    className="px-4 py-2 text-white text-sm font-bold rounded-lg disabled:opacity-50 shadow-xs"
                    style={{ backgroundColor: theme.accent }}
                  >
                    Benutzer hinzufügen
                  </button>
                </div>

                <div className={`border ${theme.border} rounded-xl overflow-hidden flex flex-col`}>
                  <div className={`p-4 border-b ${theme.border} ${theme.bgSidebar} flex items-center justify-between gap-4`}>
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={users.filter(u => !u.isAdmin && u.status !== 'ADMIN' && u.username.toLowerCase() !== 'gustavd').length > 0 && selectedUsers.length === users.filter(u => !u.isAdmin && u.status !== 'ADMIN' && u.username.toLowerCase() !== 'gustavd').length}
                        onChange={handleSelectAllUsers}
                        className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className={`text-sm font-bold ${theme.textMain}`}>Alle Benutzer auswählen</span>
                    </div>
                    {selectedUsers.length > 0 && (
                      <div className="flex items-center gap-2">
                        <span className={`text-xs ${theme.textMuted} mr-2`}>{selectedUsers.length} ausgewählt:</span>
                        <button onClick={() => handleBulkUpdateStatus('ACTIVE')} className="px-2 py-1 text-xs font-bold bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 rounded cursor-pointer">Aktiv</button>
                        <button onClick={() => handleBulkUpdateStatus('READ_ONLY')} className="px-2 py-1 text-xs font-bold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 rounded cursor-pointer">Nur Lesen</button>
                        <button onClick={() => handleBulkUpdateStatus('BLOCKED')} className="px-2 py-1 text-xs font-bold bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400 rounded cursor-pointer">Sperren</button>
                      </div>
                    )}
                  </div>
                  
                  <div className={`divide-y ${theme.border}`}>
                    {users.map(u => {
                      const isUserAdmin = u.isAdmin || u.status === 'ADMIN' || u.username.toLowerCase() === 'gustavd';
                      return (
                        <div key={u.username} className={`p-4 flex items-center justify-between ${theme.bgInput} hover:bg-black/5 dark:hover:bg-white/5`}>
                          <div className="flex items-center gap-3">
                            <input
                              type="checkbox"
                              checked={selectedUsers.includes(u.username)}
                              onChange={() => handleSelectUser(u.username)}
                              disabled={isUserAdmin}
                              className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-30"
                            />
                            <div>
                              <div className="flex items-center gap-2">
                                <span className={`font-bold text-base ${theme.textMain}`}>{u.username}</span>
                              </div>
                              <div className={`text-xs font-semibold mt-1 ${isUserAdmin ? 'text-purple-500' : u.status === 'ACTIVE' ? 'text-emerald-500' : u.status === 'READ_ONLY' ? 'text-amber-500' : 'text-rose-500'}`}>
                                {isUserAdmin ? 'Admin' : u.status === 'ACTIVE' ? 'Aktiv' : u.status === 'READ_ONLY' ? 'Nur Lesezugriff' : 'Gesperrt'}
                              </div>
                            </div>
                          </div>
                          
                          <div className="flex items-center gap-2">
                            {!isUserAdmin ? (
                              <>
                                <select
                                  value={u.status || 'ACTIVE'}
                                  onChange={(e) => handleUpdateStatus(u.username, e.target.value as any)}
                                  className={`text-xs px-2 py-1.5 rounded-lg border ${theme.border} ${theme.bgModal} ${theme.textMain} focus:outline-none`}
                                >
                                  <option value="ACTIVE">Aktiv</option>
                                  <option value="READ_ONLY">Nur Lesen</option>
                                  <option value="BLOCKED">Sperren</option>
                                </select>
                                <button
                                  onClick={() => handleResetPin(u.username)}
                                  title="Passwort zurücksetzen"
                                  className="px-2 py-1.5 text-xs font-semibold text-rose-500 bg-rose-50 dark:bg-rose-950/30 rounded-lg hover:bg-rose-100 dark:hover:bg-rose-900/50 transition-colors flex items-center gap-1.5 cursor-pointer"
                                >
                                  <KeyRound className="w-3.5 h-3.5" />
                                  Passwort zurücksetzen
                                </button>
                                <button
                                  onClick={() => handleDeleteUser(u.username)}
                                  title="Benutzer löschen"
                                  className="p-1.5 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/30 rounded-lg transition-colors cursor-pointer"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </>
                            ) : (
                              <span className={`text-xs font-medium ${theme.textFaint} italic px-2`}>
                                Nur in der DB veränderbar
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'categories' && (
              <div className="space-y-6">
                <div>
                  <h3 className={`font-bold text-xl ${theme.textMain} mb-1`}>Globale Kategorien</h3>
                  <p className={`text-sm ${theme.textFaint}`}>Diese Kategorien stehen allen Benutzern zur Verfügung.</p>
                </div>

                <div className={`flex items-center gap-3 p-4 border ${theme.border} rounded-xl ${theme.bgInput}`}>
                  <input
                    type="text"
                    value={newCatName}
                    onChange={e => setNewCatName(e.target.value)}
                    placeholder="Neue Kategorie..."
                    className={`flex-1 px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-lg text-sm ${theme.textMain} focus:outline-none`}
                  />
                  <input
                    type="color"
                    value={newCatColor}
                    onChange={e => setNewCatColor(e.target.value)}
                    className="w-10 h-10 p-0 border-0 rounded cursor-pointer shrink-0"
                  />
                  <button
                    onClick={handleAddCategory}
                    disabled={!newCatName.trim()}
                    className="px-4 py-2 text-white text-sm font-bold rounded-lg disabled:opacity-50 shrink-0 shadow-xs"
                    style={{ backgroundColor: theme.accent }}
                  >
                    Hinzufügen
                  </button>
                </div>

                <div className="space-y-2">
                  {categories.map(c => (
                    <div key={c.id} className={`flex items-center justify-between p-3 border ${theme.border} rounded-xl ${theme.bgModal}`}>
                      <div className="flex flex-1 items-center gap-3 mr-4">
                        <input
                          type="color"
                          value={c.color}
                          onChange={(e) => handleUpdateCategory(c.id, c.name, e.target.value)}
                          className="w-6 h-6 p-0 border-0 rounded cursor-pointer shrink-0 bg-transparent"
                        />
                        <input 
                          type="text" 
                          value={c.name}
                          onChange={(e) => handleUpdateCategory(c.id, e.target.value, c.color)}
                          className={`font-semibold text-sm ${theme.textMain} bg-transparent border-none focus:outline-none focus:ring-1 focus:ring-[#e91e63] rounded px-1 w-full max-w-[200px]`}
                        />
                      </div>
                      <button
                        onClick={() => handleDeleteCategory(c.id)}
                        className="p-1.5 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/30 rounded-lg transition-colors shrink-0"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {activeTab === 'courses' && (
              <div className="space-y-6">
                <div>
                  <h3 className={`font-bold text-xl ${theme.textMain} mb-1 flex items-center gap-2`}>
                    <Layers className="w-5 h-5 text-[#e91e63]" />
                    Kurse & Fächer verwalten
                  </h3>
                  <p className={`text-sm ${theme.textFaint}`}>
                    Verschiebe Kurse per Drag & Drop innerhalb oder zwischen den Bereichen (LK, GK, AG).
                  </p>
                </div>

                {/* Add new course toolbar */}
                <div className={`p-4 border ${theme.border} rounded-2xl ${theme.bgInput} space-y-3`}>
                  <h4 className={`text-xs font-bold ${theme.textMuted} uppercase tracking-wider`}>Neuen Kurs anlegen</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5">
                    <input
                      type="text"
                      value={newCourseName}
                      onChange={e => setNewCourseName(e.target.value)}
                      placeholder="Kursname (z.B. MA1, de1, Chor)"
                      className={`px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-xl text-sm ${theme.textMain} focus:outline-none focus:ring-1 focus:ring-[#e91e63]`}
                    />
                    <input
                      type="text"
                      value={newCourseTeacher}
                      onChange={e => setNewCourseTeacher(e.target.value)}
                      placeholder="Lehrer (z.B. Kön, Hof1)"
                      className={`px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-xl text-sm ${theme.textMain} focus:outline-none focus:ring-1 focus:ring-[#e91e63]`}
                    />
                    <select
                      value={newCourseType}
                      onChange={e => setNewCourseType(e.target.value as any)}
                      className={`px-3 py-2 border ${theme.borderInput} ${theme.bgModal} rounded-xl text-sm font-semibold ${theme.textMain} focus:outline-none focus:ring-1 focus:ring-[#e91e63] cursor-pointer`}
                    >
                      <option value="LK">Leistungskurs (LK)</option>
                      <option value="GK">Grundkurs (GK)</option>
                      <option value="AG">Arbeitsgemeinschaft (AG)</option>
                    </select>
                    <button
                      type="button"
                      onClick={handleAddCourse}
                      disabled={!newCourseName.trim() || !newCourseTeacher.trim()}
                      className="inline-flex items-center justify-center gap-1.5 px-4 py-2 text-white text-sm font-bold rounded-xl disabled:opacity-50 shadow-xs cursor-pointer transition-all active:scale-95"
                      style={{ backgroundColor: theme.accent }}
                    >
                      <Plus className="w-4 h-4" />
                      Hinzufügen
                    </button>
                  </div>
                </div>

                {/* Course Sections: LK, GK, AG */}
                {(
                  [
                    { type: 'LK' as const, title: 'Leistungskurse (LK)', list: lks, color: '#e91e63' },
                    { type: 'GK' as const, title: 'Grundkurse (GK)', list: gks, color: '#3b82f6' },
                    { type: 'AG' as const, title: 'Arbeitsgemeinschaften (AG)', list: ags, color: '#10b981' }
                  ]
                ).map(({ type, title, list, color }) => {
                  const isTargetSection = dragOverSection === type;
                  return (
                    <div
                      key={type}
                      onDragOver={(e) => handleDragOverSection(e, type)}
                      onDrop={(e) => handleDropOnSection(e, type)}
                      className={`p-4 rounded-2xl border transition-all ${
                        isTargetSection ? 'border-[#e91e63] bg-pink-500/5 ring-1 ring-[#e91e63]' : `${theme.border} ${theme.bgSidebar}`
                      }`}
                    >
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
                          <h4 className={`text-xs font-bold ${theme.textMain} uppercase tracking-wider`}>{title}</h4>
                          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${isDark ? 'bg-[#282828] text-neutral-300' : 'bg-neutral-200/70 text-neutral-700'}`}>
                            {list.length} {list.length === 1 ? 'Kurs' : 'Kurse'}
                          </span>
                        </div>
                        <span className={`text-[11px] ${theme.textFaint}`}>
                          Karten greifen & verschieben
                        </span>
                      </div>

                      {list.length === 0 ? (
                        <div
                          className={`flex items-center justify-center p-6 border-2 border-dashed rounded-xl ${
                            isDark ? 'border-neutral-700 text-neutral-500' : 'border-neutral-300 text-neutral-400'
                          } text-xs font-medium text-center`}
                        >
                          Kurse hierher ziehen, um sie als {type} festzulegen
                        </div>
                      ) : (
                        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5">
                          {list.map((c) => {
                            const isBeingDragged = draggedCourseId === c.id;
                            const isDragOver = dragOverCourseId === c.id;
                            return (
                              <div
                                key={c.id}
                                draggable={true}
                                onDragStart={(e) => handleDragStart(e, c.id)}
                                onDragEnd={handleDragEnd}
                                onDragOver={(e) => handleDragOverCard(e, c.id, type)}
                                onDrop={(e) => handleDropOnCard(e, c)}
                                className={`group relative p-2.5 rounded-xl border transition-all flex flex-col justify-between select-none ${
                                  isBeingDragged
                                    ? 'opacity-30 scale-95 border-dashed border-[#e91e63]'
                                    : isDragOver
                                    ? 'ring-2 ring-[#e91e63] bg-pink-500/10 border-transparent shadow-md'
                                    : `${isDark ? 'bg-[#222] border-[#383838] hover:border-[#555]' : 'bg-white border-[#e5e7eb] hover:border-[#cbd5e1]'} shadow-2xs`
                                }`}
                              >
                                {/* Top Row: Drag Handle + Inline Name & Teacher + Delete */}
                                <div className="flex items-start justify-between gap-1 mb-2">
                                  <div className="flex items-center gap-1.5 flex-1 min-w-0">
                                    <div
                                      className="cursor-grab active:cursor-grabbing p-0.5 text-neutral-400 hover:text-neutral-200 shrink-0"
                                      title="Verschieben"
                                    >
                                      <GripVertical className="w-3.5 h-3.5" />
                                    </div>
                                    <div className="flex flex-col flex-1 min-w-0">
                                      <div className="flex items-center gap-1">
                                        <input
                                          type="text"
                                          value={c.name}
                                          onChange={(e) => handleUpdateCourse(c.id, e.target.value, c.teacher, c.type)}
                                          className={`font-bold text-sm ${theme.textMain} bg-transparent border-none p-0 focus:outline-none focus:ring-1 focus:ring-[#e91e63] rounded w-full`}
                                          placeholder="Name"
                                          title="Kursname bearbeiten"
                                        />
                                      </div>
                                      <div className="flex items-center gap-0.5 text-xs text-neutral-400">
                                        <span>(</span>
                                        <input
                                          type="text"
                                          value={c.teacher}
                                          onChange={(e) => handleUpdateCourse(c.id, c.name, e.target.value, c.type)}
                                          className={`text-xs ${theme.textMuted} bg-transparent border-none p-0 focus:outline-none focus:ring-1 focus:ring-[#e91e63] rounded w-full`}
                                          placeholder="Lehrer"
                                          title="Lehrer bearbeiten"
                                        />
                                        <span>)</span>
                                      </div>
                                    </div>
                                  </div>

                                  <button
                                    type="button"
                                    onClick={() => handleDeleteCourse(c.id)}
                                    className="opacity-0 group-hover:opacity-100 p-1 text-rose-400 hover:text-rose-600 hover:bg-rose-500/10 rounded-md transition-all shrink-0 cursor-pointer"
                                    title="Kurs löschen"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" />
                                  </button>
                                </div>

                                {/* Bottom Controls: Type Selector & Nudge Left/Right */}
                                <div className="flex items-center justify-between pt-2 border-t border-neutral-200/40 dark:border-neutral-800/80 gap-1 text-[11px]">
                                  <select
                                    value={c.type}
                                    onChange={(e) => handleChangeCourseType(c.id, e.target.value as any)}
                                    className={`text-[10px] font-bold uppercase rounded px-1 py-0.5 bg-black/5 dark:bg-white/5 border border-neutral-300/40 dark:border-neutral-700/50 ${theme.textMain} focus:outline-none cursor-pointer`}
                                    title="Bereich wechseln (LK / GK / AG)"
                                  >
                                    <option value="LK">LK</option>
                                    <option value="GK">GK</option>
                                    <option value="AG">AG</option>
                                  </select>

                                  <div className="flex items-center gap-0.5">
                                    <button
                                      type="button"
                                      onClick={() => handleMoveWithinSection(c.id, 'left')}
                                      className={`p-1 rounded transition-colors ${theme.bgHover} ${theme.textMuted} cursor-pointer`}
                                      title="Nach links verschieben"
                                    >
                                      <ArrowLeft className="w-3 h-3" />
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => handleMoveWithinSection(c.id, 'right')}
                                      className={`p-1 rounded transition-colors ${theme.bgHover} ${theme.textMuted} cursor-pointer`}
                                      title="Nach rechts verschieben"
                                    >
                                      <ArrowRight className="w-3 h-3" />
                                    </button>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
