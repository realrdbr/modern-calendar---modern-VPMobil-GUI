import { useState, FormEvent } from 'react';
import { UserPreferences, Course, EventCategory, COURSES as DEFAULT_COURSES } from '../types';
import { submitFeedback } from '../lib/api';
import { X, Check, AlertCircle } from 'lucide-react';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  preferences: UserPreferences;
  hasPin?: boolean;
  initialCourses?: string[];
  allCourses?: Course[];
  categories?: EventCategory[];
  defaultTab?: 'allgemein' | 'design' | 'kurse' | 'feedback';
  onSave: (prefs: UserPreferences, newPin?: string, newCourses?: string[], oldPin?: string) => void;
  username: string;
}

export default function SettingsModal({
  isOpen,
  onClose,
  preferences,
  hasPin,
  initialCourses = [],
  allCourses = DEFAULT_COURSES,
  categories = [
    { id: 'KLAUSUR', name: 'Klausuren', color: '#e65176' },
    { id: 'HAUSAUFGABE', name: 'Hausaufgaben', color: '#59b3cb' },
    { id: 'SONSTIGES', name: 'Sonstiges', color: '#3d60c7' },
    { id: 'FERIEN', name: 'Freie Tage / Ferien', color: '#f1c40f' }
  ],
  defaultTab = 'allgemein',
  onSave,
  username
}: Props) {
  const [activeTab, setActiveTab] = useState<'allgemein' | 'design' | 'kurse' | 'feedback'>(defaultTab);

  // Feedback state
  const [feedbackText, setFeedbackText] = useState('');
  const [feedbackStatus, setFeedbackStatus] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');

  // Design / Allgemein State
  const [darkMode, setDarkMode] = useState(preferences.darkMode);
  const [themeMode, setThemeMode] = useState<'system' | 'light' | 'dark'>(preferences.themeMode || 'system');
  const [accentColor, setAccentColor] = useState(preferences.accentColor);
  const [colorKlausur, setColorKlausur] = useState(preferences.colorKlausur || '#e65176');
  const [colorHausaufgabe, setColorHausaufgabe] = useState(preferences.colorHausaufgabe || '#59b3cb');
  const [colorSonstiges, setColorSonstiges] = useState(preferences.colorSonstiges || '#3d60c7');
  const [colorFerien, setColorFerien] = useState(preferences.colorFerien || '#f1c40f');
  
  // Category colors map (holds ONLY explicit user custom overrides)
  const [categoryColors, setCategoryColors] = useState<Record<string, string>>(() => {
    return preferences.categoryColors ? { ...preferences.categoryColors } : {};
  });

  const [oldPin, setOldPin] = useState('');
  const [newPin, setNewPin] = useState('');

  // Courses State - normalize legacy IDs like 'Chor' -> 'CHO'
  const normalizedInitial = (initialCourses || []).map(id => id === 'Chor' ? 'CHO' : id);
  const [selectedCourses, setSelectedCourses] = useState<Set<string>>(new Set(normalizedInitial));

  if (!isOpen) return null;

  const toggleCourse = (id: string) => {
    const targetId = id === 'Chor' ? 'CHO' : id;
    const newSelected = new Set<string>();
    
    selectedCourses.forEach(item => {
      const norm = item === 'Chor' ? 'CHO' : item;
      newSelected.add(norm);
    });

    if (newSelected.has(targetId)) {
      newSelected.delete(targetId);
      if (targetId === 'CHO') newSelected.delete('Chor');
    } else {
      newSelected.add(targetId);
    }
    setSelectedCourses(newSelected);
  };

  const handleCategoryColorChange = (catId: string, color: string) => {
    setCategoryColors(prev => ({ ...prev, [catId]: color }));
    if (catId === 'KLAUSUR') setColorKlausur(color);
    if (catId === 'HAUSAUFGABE') setColorHausaufgabe(color);
    if (catId === 'SONSTIGES') setColorSonstiges(color);
    if (catId === 'FERIEN') setColorFerien(color);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSave(
      {
        darkMode,
        themeMode,
        accentColor,
        colorKlausur: categoryColors['KLAUSUR'] || colorKlausur,
        colorHausaufgabe: categoryColors['HAUSAUFGABE'] || colorHausaufgabe,
        colorSonstiges: categoryColors['SONSTIGES'] || colorSonstiges,
        colorFerien: categoryColors['FERIEN'] || colorFerien,
        categoryColors
      },
      newPin.trim() !== '' ? newPin.trim() : undefined,
      Array.from(selectedCourses),
      oldPin.trim() !== '' ? oldPin.trim() : undefined
    );
    onClose();
  };

  const handleFeedbackSubmit = async () => {
    if (!feedbackText.trim()) return;
    setFeedbackStatus('sending');
    try {
      await submitFeedback(username, feedbackText.trim());
      setFeedbackStatus('success');
      setFeedbackText('');
      setTimeout(() => {
        setFeedbackStatus('idle');
      }, 3000);
    } catch (e) {
      console.error('Failed to submit feedback:', e);
      setFeedbackStatus('error');
    }
  };

  const lks = allCourses.filter((c) => c.type === 'LK');
  const gks = allCourses.filter((c) => c.type === 'GK');
  const ags = allCourses.filter((c) => c.type === 'AG');

  const isDark = themeMode === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    : themeMode === 'dark';
  const theme = {
    bgModal: isDark ? 'bg-[#1e1e1e]' : 'bg-white',
    bgSidebar: isDark ? 'bg-[#181818]' : 'bg-[#fafafa]',
    bgHover: isDark ? 'hover:bg-[#282828]' : 'hover:bg-[#f3f4f6]',
    bgInput: isDark ? 'bg-[#141414]' : 'bg-white',
    border: isDark ? 'border-[#333]' : 'border-[#e5e7eb]',
    borderInput: isDark ? 'border-[#444]' : 'border-[#d1d5db]',
    textMain: isDark ? 'text-[#f3f4f6]' : 'text-[#1f2937]',
    textMuted: isDark ? 'text-[#9ca3af]' : 'text-[#4b5563]',
    textFaint: isDark ? 'text-[#6b7280]' : 'text-[#6b7280]',
    overlay: isDark ? 'bg-black/70' : 'bg-black/50'
  };

  const tabList: Array<{ id: 'kurse' | 'allgemein' | 'design' | 'feedback'; label: string }> = [
    { id: 'kurse', label: 'Meine Kurse' },
    { id: 'allgemein', label: hasPin ? 'PIN ändern' : 'PIN vergeben' },
    { id: 'design', label: 'Aussehen' },
    { id: 'feedback', label: 'Kritik' }
  ];

  const renderCourseSection = (title: string, courses: Course[]) => (
    <div className="mb-6">
      <h4 className={`text-xs font-bold ${theme.textMuted} mb-3 uppercase tracking-wider`}>{title}</h4>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        {courses.map((course) => {
          const isSelected = selectedCourses.has(course.id);
          return (
            <button
              key={course.id}
              type="button"
              onClick={() => toggleCourse(course.id)}
              className={`text-left p-2.5 rounded-md border transition-colors cursor-pointer ${
                isSelected
                  ? 'border-opacity-100 shadow-xs'
                  : `${isDark ? 'bg-[#222] border-[#383838] hover:border-[#555]' : 'bg-white border-[#e5e7eb] hover:border-[#cbd5e1]'}`
              }`}
              style={{
                backgroundColor: isSelected ? `${accentColor}18` : undefined,
                borderColor: isSelected ? accentColor : undefined
              }}
            >
              <div
                className="font-semibold text-sm leading-snug"
                style={{ color: isSelected ? accentColor : isDark ? '#eee' : '#1f2937' }}
              >
                {course.name}
              </div>
              <div
                className={`text-xs mt-0.5 ${isSelected ? 'opacity-90 font-medium' : theme.textFaint}`}
                style={{ color: isSelected ? accentColor : undefined }}
              >
                {course.teacher}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className={`fixed inset-0 z-50 flex items-center justify-center ${theme.overlay} p-3 sm:p-4`}>
      <div
        className={`${theme.bgModal} shadow-lg w-full max-w-[880px] max-h-[92vh] border ${theme.border} rounded-lg flex flex-col md:flex-row overflow-hidden`}
      >
        {/* MOBILE TOP HEADER */}
        <div className={`flex items-center justify-between px-4 py-3.5 border-b ${theme.border} md:hidden ${theme.bgSidebar}`}>
          <div className="flex items-center gap-2">
            <span className="font-bold text-base text-gray-900 dark:text-white" style={{ color: isDark ? '#fff' : '#111' }}>
              Einstellungen
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Schließen"
            className={`p-1.5 rounded-lg ${theme.bgHover} ${theme.textMuted} hover:${theme.textMain} transition-colors`}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* SIDEBAR TABS */}
        <div
          className={`w-full md:w-60 ${theme.bgSidebar} border-b md:border-b-0 md:border-r ${theme.border} flex flex-row md:flex-col shrink-0 overflow-x-auto p-1.5 md:p-3 gap-1`}
        >
          <div className={`px-3 py-3 font-bold text-lg ${theme.textMain} hidden md:block border-b ${theme.border} mb-2`}>
            Einstellungen
          </div>

          {tabList.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center justify-center md:justify-start px-3.5 py-2.5 text-xs sm:text-sm font-semibold rounded-xl transition-all whitespace-nowrap ${
                  isActive
                    ? 'shadow-xs border'
                    : `${theme.textMuted} ${theme.bgHover} border border-transparent`
                }`}
                style={{
                  backgroundColor: isActive ? `${accentColor}15` : undefined,
                  borderColor: isActive ? accentColor : 'transparent',
                  color: isActive ? accentColor : undefined
                }}
              >
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* CONTENT & FORM AREA */}
        <div className="flex-1 flex flex-col min-h-0">
          {/* DESKTOP TOP HEADER */}
          <div className={`hidden md:flex items-center justify-between px-6 py-4 border-b ${theme.border} ${theme.bgModal}`}>
            <h2 className={`text-base font-bold ${theme.textMain}`}>
              {tabList.find((t) => t.id === activeTab)?.label}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Schließen"
              className={`p-1.5 rounded-lg ${theme.bgHover} ${theme.textFaint} hover:${theme.textMain} transition-colors`}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="flex-1 flex flex-col min-h-0">
            <div className="flex-1 overflow-y-auto p-5 sm:p-6 md:p-8">
              {/* TAB 1: KURSE */}
              {activeTab === 'kurse' && (
                <div className="space-y-4 animate-in fade-in duration-200">
                  <div className="mb-2">
                    <h3 className={`font-bold text-xl sm:text-2xl ${theme.textMain}`}>Kurswahl</h3>
                    <p className={`text-xs sm:text-sm ${theme.textFaint} mt-1`}>
                      Wähle alle deine LKs, GKs und AGs aus, um die zugehörigen Termine im Kalender zu sehen.
                    </p>
                  </div>

                  {renderCourseSection('Leistungskurse (LK)', lks)}
                  {renderCourseSection('Grundkurse (GK)', gks)}
                  {renderCourseSection('Arbeitsgemeinschaften (AG)', ags)}
                </div>
              )}

              {/* TAB 2: SICHERHEIT */}
              {activeTab === 'allgemein' && (
                <div className="space-y-6 animate-in fade-in duration-200">
                  <div>
                    <h3 className={`font-bold text-xl sm:text-2xl ${theme.textMain} mb-1`}>Sicherheit</h3>
                    <p className={`text-xs sm:text-sm ${theme.textFaint}`}>
                      Schütze deinen Kalender-Account mit einem persönlichen PIN-Code.
                    </p>
                  </div>
                  <div className="max-w-xs space-y-4">
                    {hasPin && (
                      <div>
                        <label className={`block text-xs sm:text-sm font-semibold ${theme.textMuted} mb-1.5`}>
                          Aktueller PIN-Code
                        </label>
                        <input
                          type="password"
                          maxLength={4}
                          value={oldPin}
                          onChange={(e) => setOldPin(e.target.value)}
                          placeholder="••••"
                          className={`w-full px-4 py-2.5 ${theme.bgInput} border ${theme.borderInput} ${theme.textMain} rounded-xl focus:outline-none transition-colors tracking-[0.5em] text-lg text-center placeholder-opacity-40`}
                        />
                      </div>
                    )}

                    <div>
                      <label className={`block text-xs sm:text-sm font-semibold ${theme.textMuted} mb-1.5`}>
                        {hasPin ? 'Neuer PIN-Code (optional)' : '4-stelliger PIN-Code festlegen'}
                      </label>
                      <input
                        type="password"
                        maxLength={4}
                        value={newPin}
                        onChange={(e) => setNewPin(e.target.value)}
                        placeholder="••••"
                        className={`w-full px-4 py-2.5 ${theme.bgInput} border ${theme.borderInput} ${theme.textMain} rounded-xl focus:outline-none transition-colors tracking-[0.5em] text-lg text-center placeholder-opacity-40`}
                        style={{ borderBottomColor: newPin.length > 0 ? accentColor : undefined }}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: AUSSEHEN */}
              {activeTab === 'design' && (
                <div className="space-y-6 animate-in fade-in duration-200 max-w-lg">
                  <div>
                    <h3 className={`font-bold text-xl sm:text-2xl ${theme.textMain}`}>Aussehen</h3>
                  </div>

                  <div
                    className={`flex flex-col sm:flex-row sm:items-center justify-between p-4 ${theme.bgInput} rounded-md border ${theme.borderInput} gap-3`}
                  >
                    <div className={`text-sm font-bold ${theme.textMain}`}>Erscheinungsbild</div>
                    
                    <div
                      className={`inline-flex p-1 rounded-xl shrink-0 self-start sm:self-auto transition-colors ${
                        isDark ? 'bg-[#181818] border border-[#333]' : 'bg-[#e5e7eb] border border-[#d1d5db]'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => { setThemeMode('system'); setDarkMode(window.matchMedia('(prefers-color-scheme: dark)').matches); }}
                        className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                          themeMode === 'system'
                            ? 'bg-white text-gray-900 shadow-xs'
                            : `${theme.textMuted} hover:${theme.textMain}`
                        }`}
                      >
                        System
                      </button>
                      <button
                        type="button"
                        onClick={() => { setThemeMode('light'); setDarkMode(false); }}
                        className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${themeMode === 'light' ? 'bg-white text-gray-900 shadow-xs' : `${theme.textMuted} hover:${theme.textMain}`}`}
                      >Hell</button>
                      <button
                        type="button"
                        onClick={() => { setThemeMode('dark'); setDarkMode(true); }}
                        className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                          themeMode === 'dark'
                            ? 'text-white shadow-xs'
                            : `${theme.textMuted} hover:${theme.textMain}`
                        }`}
                        style={{
                          backgroundColor: themeMode === 'dark' ? accentColor : 'transparent'
                        }}
                      >
                        Dunkel
                      </button>
                    </div>
                  </div>

                  <div>
                    <h4 className={`font-bold text-xs ${theme.textMuted} mb-3 uppercase tracking-wider`}>
                      Persönliche Farben
                    </h4>
                    <div className="space-y-2.5">
                      <div
                        className={`flex items-center justify-between p-3 ${theme.bgInput} rounded-xl border ${theme.borderInput}`}
                      >
                        <label className={`text-xs sm:text-sm font-medium ${theme.textMain}`}>Haupt-Akzentfarbe</label>
                        <input
                          type="color"
                          value={accentColor}
                          onChange={(e) => setAccentColor(e.target.value)}
                          className="w-8 h-8 cursor-pointer rounded-lg border-0 p-0 bg-transparent"
                        />
                      </div>

                      {categories.map(cat => {
                        const isOverridden = !!categoryColors[cat.id];
                        const currentColor = categoryColors[cat.id] || cat.color || '#3d60c7';

                        return (
                          <div
                            key={cat.id}
                            className={`flex items-center justify-between p-3 ${theme.bgInput} rounded-xl border ${theme.borderInput}`}
                          >
                            <label className={`text-xs sm:text-sm font-medium ${theme.textMain}`}>{cat.name}</label>
                            
                            <div className="flex items-center gap-2">
                              {isOverridden && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    const next = { ...categoryColors };
                                    delete next[cat.id];
                                    setCategoryColors(next);
                                  }}
                                  title="Auf Standard zurücksetzen"
                                  className={`text-xs ${theme.textFaint} hover:${theme.textMain} underline cursor-pointer`}
                                >
                                  Zurücksetzen
                                </button>
                              )}
                              <input
                                type="color"
                                value={currentColor}
                                onChange={(e) => handleCategoryColorChange(cat.id, e.target.value)}
                                className="w-8 h-8 cursor-pointer rounded-lg border-0 p-0 bg-transparent"
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 4: KRITIK */}
              {activeTab === 'feedback' && (
                <div className="space-y-5 animate-in fade-in duration-200 max-w-lg">
                  <div>
                    <h3 className={`font-bold text-xl sm:text-2xl ${theme.textMain} mb-1`}>Kritik</h3>
                    <p className={`text-xs sm:text-sm ${theme.textFaint}`}>
                      Gib direktes Feedback, melde gefundene Bugs oder schlage neue Funktionen für den Jahrgangskalender vor.
                    </p>
                  </div>

                  <div className="flex flex-col space-y-3.5">
                    <label className={`text-xs sm:text-sm font-semibold ${theme.textMuted}`}>
                      Deine Nachricht (wird als {username} gespeichert)
                    </label>
                    <textarea
                      value={feedbackText}
                      onChange={(e) => setFeedbackText(e.target.value)}
                      placeholder="Was läuft noch nicht rund? Welche Features fehlen dir?"
                      rows={4}
                      className={`w-full px-4 py-3 ${theme.bgInput} border ${theme.borderInput} ${theme.textMain} rounded-xl focus:outline-none transition-colors resize-none text-sm`}
                      style={{ borderBottomColor: feedbackText.length > 0 ? accentColor : undefined }}
                    />

                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={handleFeedbackSubmit}
                        disabled={feedbackStatus === 'sending' || !feedbackText.trim()}
                        className="py-2.5 px-5 text-white text-xs sm:text-sm font-semibold rounded-xl transition-all disabled:opacity-50 flex items-center gap-2 shadow-xs"
                        style={{ backgroundColor: accentColor }}
                      >
                        {feedbackStatus === 'sending' ? (
                          'Wird gesendet...'
                        ) : feedbackStatus === 'success' ? (
                          <>
                            <Check className="w-4 h-4" /> Gesendet
                          </>
                        ) : (
                          'Absenden'
                        )}
                      </button>

                      {feedbackStatus === 'success' && (
                        <span className="text-xs text-emerald-600 font-medium">Vielen Dank für deine Rückmeldung!</span>
                      )}
                      {feedbackStatus === 'error' && (
                        <span className="text-xs text-rose-600 font-medium flex items-center gap-1">
                          <AlertCircle className="w-4 h-4" /> Fehler beim Senden. Bitte erneut versuchen.
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* MODAL FOOTER */}
            <div className={`flex items-center justify-end p-4 sm:p-5 border-t ${theme.border} ${theme.bgModal} gap-2`}>
              <button
                type="button"
                onClick={onClose}
                className={`px-5 py-2.5 text-xs sm:text-sm font-semibold ${theme.textMuted} ${theme.bgHover} rounded-xl transition-colors cursor-pointer`}
              >
                Abbrechen
              </button>
              <button
                type="submit"
                className="px-6 py-2.5 text-xs sm:text-sm font-bold text-white rounded-xl transition-opacity hover:opacity-90 shadow-xs cursor-pointer"
                style={{ backgroundColor: accentColor }}
              >
                Speichern
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
