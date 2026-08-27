import { useState, useEffect, useMemo, useRef, MouseEvent, TouchEvent } from 'react';
import { format, startOfMonth, endOfMonth, eachDayOfInterval, isSameDay, addMonths, subMonths, addWeeks, subWeeks, startOfWeek, endOfWeek, isSameMonth, getISOWeek } from 'date-fns';
import { de } from 'date-fns/locale';
import { fetchEvents, createEvent, updateEvent, deleteEvent, fetchCourses, fetchAdmins, fetchCategories } from '../lib/api';
import { AppEvent, Course, COURSES, User, EventCategory } from '../types';
import EventModal from './EventModal';
import SettingsModal from './SettingsModal';
import AdminModal from './AdminModal';
import { Menu, X, Settings, Shield, LogOut } from 'lucide-react';
import { VERTRETUNGSPLAN_URL } from '../lib/externalLinks';

interface Props {
  user: User;
  onUpdatePreferences: (prefs: any, newPin?: string, newCourses?: string[]) => void;
  isInitialSetup?: boolean;
  onLogout?: () => void;
}

export default function CalendarView({ user, onUpdatePreferences, isInitialSetup = false, onLogout }: Props) {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [view, setView] = useState<'month' | 'week'>('month');
  const [rawEvents, setRawEvents] = useState<AppEvent[]>([]);
  const [allCourses, setAllCourses] = useState<Course[]>(COURSES);
  const [categories, setCategories] = useState<EventCategory[]>([]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(isInitialSetup);
  const [isAdminOpen, setIsAdminOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [selectedTime, setSelectedTime] = useState<string | undefined>(undefined);
  const [editingEvent, setEditingEvent] = useState<AppEvent | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [swipePull, setSwipePull] = useState(0);
  const touchStart = useRef<{ x: number; y: number } | null>(null);

  const { preferences } = user;
  const [systemPrefersDark, setSystemPrefersDark] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
  );
  const isDark = preferences.themeMode === 'system' || !preferences.themeMode
    ? systemPrefersDark
    : preferences.themeMode === 'dark';

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setSystemPrefersDark(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  // Reactively filter events based on current user's enrolled courses
  const events = useMemo(() => {
    const userCourses = user.courses || [];
    const hasCho = userCourses.includes('CHO') || userCourses.includes('Chor');

    return rawEvents.filter(e => {
      // 1. Allgemein events are visible to every student
      if (!e.courseId || e.courseId === 'ALLGEMEIN') {
        return true;
      }
      // 2. Chor AG compatibility check (handles 'CHO' and 'Chor')
      if (e.courseId === 'CHO' || e.courseId === 'Chor') {
        return hasCho;
      }
      // 3. Exact course match (preserves LK uppercase vs GK lowercase, e.g. DE1 vs de1)
      return userCourses.includes(e.courseId);
    });
  }, [rawEvents, user.courses]);
  
  // Theming variables
  const theme = {
    bgApp: isDark ? 'bg-[#09090b]' : 'bg-[#ffffff]',
    bgSidebar: isDark ? 'bg-[#111113]' : 'bg-[#f7f7f8]',
    bgToolbar: isDark ? 'bg-[#111113]' : 'bg-[#fafafa]',
    bgGridHeader: isDark ? 'bg-[#18181b]' : 'bg-[#fafafa]',
    bgCell: isDark ? 'bg-[#09090b]' : 'bg-white',
    bgCellAlt: isDark ? 'bg-[#111113]' : 'bg-[#f7f7f8]',
    bgCellWeekend: isDark ? 'bg-[#0d0d0f]' : 'bg-[#fafafa]',
    bgCellToday: isDark ? 'bg-[#102522]' : 'bg-[#f0fdfa]',
    textMain: isDark ? 'text-[#fafafa]' : 'text-[#18181b]',
    textMuted: isDark ? 'text-[#a1a1aa]' : 'text-[#52525b]',
    textFaint: isDark ? 'text-[#71717a]' : 'text-[#71717a]',
    border: isDark ? 'border-[#27272a]' : 'border-[#e4e4e7]',
    accent: preferences.accentColor
  };

  useEffect(() => {
    loadEvents();
    loadCourses();
    checkAdmin();
    loadCategories();
  }, []);

  const loadCategories = async () => {
    try {
      const list = await fetchCategories();
      setCategories(list);
    } catch (e) {
      console.error(e);
    }
  };

  const checkAdmin = async () => {
    try {
      if (user.status === 'ADMIN' || user.isAdmin) {
        setIsAdmin(true);
        return;
      }
      const admins = await fetchAdmins();
      setIsAdmin(admins.map((a: string) => a.toLowerCase()).includes(user.username.toLowerCase()));
    } catch (e) {}
  };

  const loadCourses = async () => {
    try {
      const list = await fetchCourses();
      if (Array.isArray(list) && list.length > 0) {
        setAllCourses(list);
      }
    } catch (e) {
      console.error('Failed to load courses:', e);
    }
  };

  const loadEvents = async () => {
    try {
      setLoading(true);
      const allEvents: AppEvent[] = await fetchEvents();
      setRawEvents(allEvents);
    } catch (e) {
      console.error('Failed to load events:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let disposed = false;
    let refreshing = false;
    const refreshCalendarData = async () => {
      if (disposed || refreshing) return;
      refreshing = true;
      try {
        await Promise.all([loadEvents(), loadCategories()]);
      } finally {
        refreshing = false;
      }
    };
    const stream = new EventSource('/api/events/stream');
    stream.addEventListener('calendar-change', () => void refreshCalendarData());
    const handleOnline = () => void refreshCalendarData();
    window.addEventListener('online', handleOnline);
    return () => {
      disposed = true;
      stream.close();
      window.removeEventListener('online', handleOnline);
    };
  }, []);

  const handleSaveEvent = async (eventData: Partial<AppEvent>) => {
    try {
      if (editingEvent) {
        await updateEvent(editingEvent.id, eventData);
      } else {
        await createEvent({ ...eventData, author: user.username });
      }
      await loadEvents();
      setIsModalOpen(false);
      setEditingEvent(undefined);
    } catch (e) {
      console.error('Failed to save event:', e);
      alert('Fehler beim Speichern des Termins.');
    }
  };

  const handleDeleteEvent = async (id: string) => {
    try {
      await deleteEvent(id);
      await loadEvents();
      setIsModalOpen(false);
      setEditingEvent(undefined);
    } catch (e) {
      console.error('Failed to delete event:', e);
    }
  };

  const isReadOnly = user.status === 'READ_ONLY';

  const openNewEventModal = (date?: Date, time?: string) => {
    if (isReadOnly) return;
    setSelectedDate(date || new Date());
    setSelectedTime(time);
    setEditingEvent(undefined);
    setIsModalOpen(true);
  };

  const openEditEventModal = (event: AppEvent, e: MouseEvent) => {
    e.stopPropagation();
    setEditingEvent(event);
    setSelectedDate(new Date(event.date));
    setIsModalOpen(true);
  };

  const navigatePrev = () => {
    if (view === 'month') setCurrentDate(subMonths(currentDate, 1));
    else setCurrentDate(subWeeks(currentDate, 1));
  };
  const navigateNext = () => {
    if (view === 'month') setCurrentDate(addMonths(currentDate, 1));
    else setCurrentDate(addWeeks(currentDate, 1));
  };
  const navigateToday = () => {
    setCurrentDate(new Date());
  };
  const handleTouchStart = (event: TouchEvent<HTMLDivElement>) => {
    if ((navigator.maxTouchPoints || 0) < 1 || event.touches.length !== 1) return;
    touchStart.current = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    setSwipePull(0);
  };
  const handleTouchMove = (event: TouchEvent<HTMLDivElement>) => {
    const start = touchStart.current;
    if (!start || event.touches.length !== 1 || (navigator.maxTouchPoints || 0) < 1) return;
    const dx = event.touches[0].clientX - start.x;
    const dy = event.touches[0].clientY - start.y;
    if (Math.abs(dx) <= Math.abs(dy) * 1.25) return;
    setSwipePull(Math.min(1, Math.max(0, (Math.abs(dx) - 60) / 90)) * (dx < 0 ? -1 : 1));
  };
  const handleTouchEnd = (event: TouchEvent<HTMLDivElement>) => {
    const start = touchStart.current;
    touchStart.current = null;
    setSwipePull(0);
    if (!start || (navigator.maxTouchPoints || 0) < 1 || event.changedTouches.length !== 1) return;
    const dx = event.changedTouches[0].clientX - start.x;
    const dy = event.changedTouches[0].clientY - start.y;
    if (Math.abs(dx) < 150 || Math.abs(dx) <= Math.abs(dy) * 1.25) return;
    if (dx < 0) navigateNext(); else navigatePrev();
  };

  const getCalendarDays = () => {
    if (view === 'month') {
      const monthStart = startOfMonth(currentDate);
      const monthEnd = endOfMonth(monthStart);
      const startDate = startOfWeek(monthStart, { weekStartsOn: 1 });
      const endDate = endOfWeek(monthEnd, { weekStartsOn: 1 });
      return eachDayOfInterval({ start: startDate, end: endDate });
    } else {
      const startDate = startOfWeek(currentDate, { weekStartsOn: 1 });
      const endDate = endOfWeek(currentDate, { weekStartsOn: 1 });
      return eachDayOfInterval({ start: startDate, end: endDate });
    }
  };

  const calendarDays = getCalendarDays();

  const getCourseName = (courseId: string) => {
    if (courseId === 'ALLGEMEIN') return 'Allgemein';
    if (courseId === 'CHO' || courseId === 'Cho' || courseId === 'Chor') return 'Chor';
    const c = allCourses.find(c => c.id === courseId);
    return c ? c.name : courseId;
  };

  const getEventTypeStyle = (type: string) => {
    let color: string | undefined = undefined;
    
    // 1. Explicit user custom override for this category
    if (preferences.categoryColors && preferences.categoryColors[type]) {
      color = preferences.categoryColors[type];
    }
    
    // 2. Admin's global category color
    if (!color) {
      const cat = categories.find(c => c.id === type);
      if (cat && cat.color) {
        color = cat.color;
      }
    }
    
    // 3. Fallback to legacy preference fields if no category match
    if (!color) {
      if (type === 'KLAUSUR' && preferences.colorKlausur) {
        color = preferences.colorKlausur;
      } else if (type === 'HAUSAUFGABE' && preferences.colorHausaufgabe) {
        color = preferences.colorHausaufgabe;
      } else if (type === 'SONSTIGES' && preferences.colorSonstiges) {
        color = preferences.colorSonstiges;
      } else if (type === 'FERIEN' && preferences.colorFerien) {
        color = preferences.colorFerien;
      }
    }
    
    if (!color) {
      color = '#3d60c7';
    }
    const hex = color.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16) || 0;
    const g = parseInt(hex.substring(2, 4), 16) || 0;
    const b = parseInt(hex.substring(4, 6), 16) || 0;
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return { backgroundColor: color, color: luminance > 0.5 ? '#000' : '#fff' };
  };

  // Group days by week
  const weeks: Date[][] = [];
  let currentWeek: Date[] = [];
  calendarDays.forEach(day => {
    currentWeek.push(day);
    if (currentWeek.length === 7) {
      weeks.push(currentWeek);
      currentWeek = [];
    }
  });

  const abWeek = getISOWeek(currentDate) % 2 === 0 ? 'A-Woche' : 'B-Woche';
  const WEEK_HOURS = ['07:00', '08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00', '22:00'];
  
  return (
    <div className={`flex h-screen ${theme.bgApp} ${theme.textMain} relative`}>
      {/* Mobile Menu Overlay */}
      {isMobileMenuOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-40 md:hidden" 
          onClick={() => setIsMobileMenuOpen(false)}
        />
      )}

      {/* Left Sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-50 transform ${isMobileMenuOpen ? 'translate-x-0' : '-translate-x-full'} md:relative md:translate-x-0 transition-transform duration-200 w-64 md:w-60 border-r ${theme.border} flex flex-col shrink-0 ${theme.bgSidebar}`}>
        <div className={`px-4 py-3 border-b ${theme.border} ${theme.bgApp} flex justify-between items-center`}>
          <div>
            <h1 className="font-semibold text-base tracking-tight mb-0.5">Jahrgangskalender</h1>
            <div className="text-sm font-medium flex items-center gap-1.5 flex-wrap">
              <span>{user.username}</span>
              {isReadOnly && (
                <span className="text-[10px] font-bold text-amber-600 dark:text-amber-400 bg-amber-500/15 px-1.5 py-0.5 rounded">
                  Nur Lesen
                </span>
              )}
            </div>
          </div>
          <button 
            className="md:hidden p-1 rounded-lg hover:opacity-75 transition-opacity" 
            onClick={() => setIsMobileMenuOpen(false)}
            aria-label="Menü schließen"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="p-3 flex-1 flex flex-col">
          <div className={`font-bold text-xs uppercase ${theme.textMuted} mb-2 flex justify-between`}>
            <span>Legende</span>
          </div>
          <div className="space-y-0.5">
            {categories.length > 0 ? (
              categories.map(cat => (
                <div key={cat.id} className="flex items-center text-sm">
                  <span 
                    className="w-3 h-3 mr-2 inline-block rounded-sm shadow-sm" 
                    style={{ backgroundColor: getEventTypeStyle(cat.id).backgroundColor }}
                  ></span>
                  {cat.name}
                </div>
              ))
            ) : (
              // Fallback during loading
              <>
                <div className="flex items-center text-sm">
                  <span className="w-3 h-3 mr-2 inline-block rounded-sm shadow-sm" style={{ backgroundColor: preferences.colorKlausur || '#e74c3c' }}></span>
                  Klausuren
                </div>
                <div className="flex items-center text-sm">
                  <span className="w-3 h-3 mr-2 inline-block rounded-sm shadow-sm" style={{ backgroundColor: preferences.colorHausaufgabe || '#3498db' }}></span>
                  Hausaufgaben
                </div>
                <div className="flex items-center text-sm">
                  <span className="w-3 h-3 mr-2 inline-block rounded-sm shadow-sm" style={{ backgroundColor: preferences.colorSonstiges || '#2ecc71' }}></span>
                  Allgemein / Sonstiges
                </div>
                <div className="flex items-center text-sm">
                  <span className="w-3 h-3 mr-2 inline-block rounded-sm shadow-sm" style={{ backgroundColor: preferences.colorFerien || '#f1c40f' }}></span>
                  Freie Tage / Ferien
                </div>
              </>
            )}
          </div>
          
          <div className={`mt-auto pt-3 flex flex-col items-stretch border-t ${theme.border} gap-1`}>
            <a
              href={VERTRETUNGSPLAN_URL}
              className="text-sm font-semibold hover:opacity-80 transition-opacity"
              style={{ color: theme.accent }}
            >
              Zum Vertretungsplan
            </a>
            {isAdmin && (
              <button 
                onClick={() => {
                  setIsMobileMenuOpen(false);
                  setIsAdminOpen(true);
                }}
                className="text-sm font-semibold flex items-center gap-2 hover:opacity-80 transition-opacity cursor-pointer"
                style={{ color: theme.accent }}
              >
                <Shield className="w-4 h-4" />
                <span>Admin Einstellungen</span>
              </button>
            )}
            <button 
              onClick={() => {
                setIsMobileMenuOpen(false);
                setIsSettingsOpen(true);
              }}
              className="text-sm font-semibold flex items-center gap-2 hover:opacity-80 transition-opacity cursor-pointer"
              style={{ color: theme.accent }}
            >
              <Settings className="w-4 h-4" />
              <span>Einstellungen</span>
            </button>
            {onLogout && (
              <button 
                onClick={() => {
                  setIsMobileMenuOpen(false);
                  onLogout();
                }}
                className="text-sm font-semibold flex items-center gap-2 hover:opacity-80 transition-opacity cursor-pointer text-red-500 hover:text-red-600"
              >
                <LogOut className="w-4 h-4" />
                <span>Abmelden</span>
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header toolbar */}
        <header className={`flex items-center px-3 py-2 border-b ${theme.border} ${theme.bgToolbar} gap-2 md:gap-3 flex-wrap`}>
          <button 
            className="md:hidden p-1 mr-1 rounded-lg hover:opacity-75"
            onClick={() => setIsMobileMenuOpen(true)}
            aria-label="Menü öffnen"
          >
            <Menu className="w-5 h-5" />
          </button>
          
          <div className={`flex items-center border ${theme.border} rounded ${theme.bgApp} overflow-hidden text-sm`}>
            <button onClick={navigatePrev} className={`px-2 md:px-3 py-1 hover:opacity-80 border-r ${theme.border}`}>&lt;</button>
            <button onClick={navigateToday} className={`px-3 md:px-4 py-1 hover:opacity-80 border-r ${theme.border}`}>Heute</button>
            <button onClick={navigateNext} className={`px-2 md:px-3 py-1 hover:opacity-80`}>&gt;</button>
          </div>
          
          <div className={`hidden md:flex items-center border ${theme.border} rounded ${theme.bgApp} overflow-hidden text-sm`}>
            <button onClick={() => setView('month')} className={`px-3 py-1 hover:opacity-80 ${view === 'month' ? 'font-bold' : ''}`} style={{ backgroundColor: view === 'month' ? theme.border : 'transparent' }}>Monat</button>
            <button onClick={() => setView('week')} className={`px-3 py-1 hover:opacity-80 border-l ${theme.border} ${view === 'week' ? 'font-bold' : ''}`} style={{ backgroundColor: view === 'week' ? theme.border : 'transparent' }}>Woche</button>
          </div>

          <div className="font-bold text-sm md:text-lg flex items-center gap-1 md:gap-2">
            {view === 'month' ? (
              format(currentDate, 'MMMM yyyy', { locale: de })
            ) : (
              <span>KW {getISOWeek(currentDate)} &bull; {abWeek}</span>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <div className={`md:hidden flex items-center border ${theme.border} rounded ${theme.bgApp} overflow-hidden text-xs`}>
              <button onClick={() => setView('month')} className={`px-2 py-1 ${view === 'month' ? 'font-bold' : ''}`} style={{ backgroundColor: view === 'month' ? theme.border : 'transparent' }}>M</button>
              <button onClick={() => setView('week')} className={`px-2 py-1 border-l ${theme.border} ${view === 'week' ? 'font-bold' : ''}`} style={{ backgroundColor: view === 'week' ? theme.border : 'transparent' }}>W</button>
            </div>
            {!isReadOnly && (
              <button
                onClick={() => openNewEventModal()}
                className="text-white px-3 py-1.5 text-sm font-semibold rounded-md border border-black/10"
                style={{ backgroundColor: theme.accent }}
              >
                + Neu
              </button>
            )}
          </div>
        </header>

        {/* Calendar Grid */}
        <div className={`relative flex-1 overflow-auto ${theme.bgApp} flex flex-col`} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}>
          {Math.abs(swipePull) > 0 && <div className={`pointer-events-none absolute top-1/2 z-20 -translate-y-1/2 text-2xl transition-opacity ${swipePull < 0 ? 'right-3' : 'left-3'}`} style={{opacity: Math.abs(swipePull)}} aria-hidden="true">{swipePull < 0 ? '→' : '←'}</div>}
          {view === 'week' ? (
            /* --- HOURLY WEEK VIEW --- */
            <div className="flex-1 overflow-auto flex flex-col min-w-[700px]">
              {/* Week View Header */}
              <div className={`grid grid-cols-[60px_1fr_1fr_1fr_1fr_1fr_1fr_1fr] border-b ${theme.border} ${theme.bgGridHeader} sticky top-0 z-20`}>
                <div className={`p-2 border-r ${theme.border} text-center font-bold text-xs ${theme.textFaint} flex items-center justify-center`}>
                  Uhrzeit
                </div>
                {calendarDays.slice(0, 7).map((day, idx) => {
                  const isToday = isSameDay(day, new Date());
                  const isWeekend = idx >= 5;
                  return (
                    <div
                      key={day.toISOString()}
                      onClick={() => openNewEventModal(day)}
                      className={`p-2 border-r ${theme.border} text-center cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition-colors`}
                    >
                      <div className={`text-xs font-semibold ${isWeekend ? theme.textFaint : theme.textMuted}`}>
                        {['Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.', 'So.'][idx]}
                      </div>
                      <div className={`inline-flex items-center justify-center text-sm font-bold mt-0.5 ${isToday ? 'text-white w-7 h-7 rounded-full' : theme.textMain}`} style={{ backgroundColor: isToday ? theme.accent : 'transparent' }}>
                        {format(day, 'd')}
                      </div>
                      {day.getDate() === 1 && (
                        <div className={`text-[10px] font-bold ${theme.textMuted} mt-0.5`}>
                          {format(day, 'MMM', {locale: de})}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Section: Ganztägige Termine */}
              <div className={`grid grid-cols-[60px_1fr_1fr_1fr_1fr_1fr_1fr_1fr] border-b ${theme.border} ${theme.bgSidebar} min-h-[48px]`}>
                <div className={`p-2 border-r ${theme.border} text-[10px] font-bold ${theme.textFaint} flex items-center justify-center text-center uppercase tracking-wider`}>
                  Ganztägig
                </div>
                {calendarDays.slice(0, 7).map((day) => {
                  const dayEvents = events.filter(e => {
                    if (e.startTime) return false;
                    const start = new Date(e.date);
                    start.setHours(0,0,0,0);
                    const end = e.endDate ? new Date(e.endDate) : new Date(start);
                    end.setHours(23,59,59,999);
                    const current = new Date(day);
                    current.setHours(12,0,0,0);
                    return current >= start && current <= end;
                  });

                  return (
                    <div
                      key={`all-day-${day.toISOString()}`}
                      onClick={() => openNewEventModal(day)}
                      className={`p-1 border-r ${theme.border} space-y-1 cursor-pointer min-h-[44px] hover:bg-black/5 dark:hover:bg-white/5 transition-colors`}
                    >
                      {dayEvents.map(e => (
                        <div
                          key={e.id}
                          onClick={(ev) => openEditEventModal(e, ev)}
                          className="rounded-sm border border-black/10 px-1.5 py-1 text-xs cursor-pointer flex flex-col justify-center leading-tight"
                          style={getEventTypeStyle(e.type)}
                          title={`${e.title} (${getCourseName(e.courseId)})`}
                        >
                          <span className="font-bold truncate">{e.title}</span>
                          <span className="opacity-90 text-[10px] truncate">{getCourseName(e.courseId)}</span>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>

              {/* Section: Hourly Timeline (07:00 - 22:00) */}
              <div className="flex-1 overflow-y-auto">
                {WEEK_HOURS.map((hour) => {
                  const hourNum = parseInt(hour.split(':')[0], 10);
                  return (
                    <div key={hour} className={`grid grid-cols-[60px_1fr_1fr_1fr_1fr_1fr_1fr_1fr] border-b ${theme.border} min-h-[60px]`}>
                      <div className={`p-1.5 border-r ${theme.border} ${theme.bgSidebar} text-xs font-mono font-bold ${theme.textFaint} flex items-start justify-center pt-2`}>
                        {hour}
                      </div>

                      {calendarDays.slice(0, 7).map((day) => {
                        const hourEvents = events.filter(e => {
                          if (!e.startTime) return false;
                          const eStart = new Date(e.date);
                          if (!isSameDay(eStart, day)) return false;
                          const eventHourNum = parseInt(e.startTime.split(':')[0], 10);
                          return eventHourNum === hourNum;
                        });

                        return (
                          <div
                            key={`${day.toISOString()}-${hour}`}
                            onClick={() => openNewEventModal(day, hour)}
                            className={`p-1 border-r ${theme.border} space-y-1 cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition-colors relative min-h-[56px]`}
                          >
                            {hourEvents.map(e => (
                              <div
                                key={e.id}
                                onClick={(ev) => openEditEventModal(e, ev)}
                                className="rounded-sm p-1.5 text-xs cursor-pointer flex flex-col justify-center leading-tight border border-black/10"
                                style={getEventTypeStyle(e.type)}
                                title={`${e.title} (${getCourseName(e.courseId)}) - ${e.startTime}${e.endTime ? ` bis ${e.endTime}` : ''}`}
                              >
                                <div className="flex items-center justify-between text-xs font-semibold opacity-95 mb-0.5">
                                  <span>{e.startTime}{e.endTime ? ` – ${e.endTime}` : ''} Uhr</span>
                                </div>
                                <span className="font-bold truncate text-[13px]">{e.title}</span>
                                <span className="opacity-90 text-[11px] truncate">{getCourseName(e.courseId)}</span>
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            /* --- MONTH VIEW --- */
            <div className="min-w-[700px] flex-1 flex flex-col">
              {/* Day Headers */}
              <div className={`grid grid-cols-[40px_1fr_1fr_1fr_1fr_1fr_1fr_1fr] border-b ${theme.border} ${theme.bgGridHeader} sticky top-0 z-20`}>
                <div className={`p-2 border-r ${theme.border}`}></div>
                {['Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.', 'So.'].map((day, idx) => (
                  <div key={day} className={`p-2 text-sm font-bold border-r ${theme.border} text-center ${idx >= 5 ? theme.textFaint : theme.textMain}`}>
                    {day}
                  </div>
                ))}
              </div>

              {/* Weeks */}
              <div className="flex-1 flex flex-col">
                {weeks.map((week, weekIdx) => {
                  const weekStart = new Date(week[0]);
                  weekStart.setHours(0,0,0,0);
                  const weekEnd = new Date(week[6]);
                  weekEnd.setHours(23,59,59,999);
                  
                  const weekEvents = events.filter(e => {
                    const start = new Date(e.date);
                    start.setHours(0,0,0,0);
                    const end = e.endDate ? new Date(e.endDate) : new Date(start);
                    end.setHours(23,59,59,999);
                    return start <= weekEnd && end >= weekStart;
                  });

                  weekEvents.sort((a, b) => {
                    const aStart = new Date(a.date).getTime();
                    const bStart = new Date(b.date).getTime();
                    if (aStart !== bStart) return aStart - bStart;
                    const aEnd = a.endDate ? new Date(a.endDate).getTime() : aStart;
                    const bEnd = b.endDate ? new Date(b.endDate).getTime() : bStart;
                    return (bEnd - bStart) - (aEnd - aStart);
                  });

                  const slots: { colStart: number, colEnd: number }[][] = [];
                  const slotted: { event: AppEvent, colStart: number, colSpan: number, rowStart: number }[] = [];

                  weekEvents.forEach(event => {
                    const start = new Date(event.date);
                    start.setHours(0,0,0,0);
                    const end = event.endDate ? new Date(event.endDate) : new Date(start);
                    end.setHours(23,59,59,999);

                    const effectiveStart = start < weekStart ? weekStart : start;
                    const effectiveEnd = end > weekEnd ? weekEnd : end;

                    const startIdx = week.findIndex(d => isSameDay(d, effectiveStart));
                    const endIdx = week.findIndex(d => isSameDay(d, effectiveEnd));

                    const colStart = (startIdx >= 0 ? startIdx : 0) + 2;
                    const colSpan = (endIdx >= 0 ? endIdx : 6) - (startIdx >= 0 ? startIdx : 0) + 1;
                    const colEnd = colStart + colSpan - 1;

                    let assignedSlot = -1;
                    for (let i = 0; i < slots.length; i++) {
                      const overlap = slots[i].some(se => !(colEnd < se.colStart || colStart > se.colEnd));
                      if (!overlap) {
                        assignedSlot = i;
                        break;
                      }
                    }

                    if (assignedSlot === -1) {
                      assignedSlot = slots.length;
                      slots.push([]);
                    }

                    slots[assignedSlot].push({ colStart, colEnd });
                    slotted.push({ event, colStart, colSpan, rowStart: assignedSlot + 2 });
                  });

                  const maxRowInWeek = slotted.length > 0 ? Math.max(...slotted.map(s => s.rowStart)) : 2;
                  const calculatedMinHeight = `${Math.max(130, (maxRowInWeek + 1) * 32 + 20)}px`;

                  return (
                    <div 
                      key={weekIdx} 
                      className={`relative border-b ${theme.border} flex-1`}
                      style={{ minHeight: calculatedMinHeight }}
                    >
                      {/* Background Layer */}
                      <div className="absolute inset-0 grid grid-cols-[40px_1fr_1fr_1fr_1fr_1fr_1fr_1fr]">
                        <div className={`border-r ${theme.border} flex justify-center pt-2 ${theme.bgSidebar} text-xs font-bold ${theme.textFaint}`}>
                          {getISOWeek(week[0])}
                        </div>
                        {week.map((day, dayIdx) => {
                          const isCurrentMonth = isSameMonth(day, currentDate);
                          const isToday = isSameDay(day, new Date());
                          const isWeekend = dayIdx >= 5;
                          let bgClass = theme.bgCell;
                          if (!isCurrentMonth) bgClass = theme.bgCellAlt;
                          else if (isToday) bgClass = theme.bgCellToday;
                          else if (isWeekend) bgClass = theme.bgCellWeekend;

                          return (
                            <div
                              key={day.toISOString()}
                              onClick={() => openNewEventModal(day)}
                              className={`border-r ${theme.border} cursor-pointer ${bgClass}`}
                            />
                          );
                        })}
                      </div>

                      {/* Foreground Layer */}
                      <div className="relative grid grid-cols-[40px_1fr_1fr_1fr_1fr_1fr_1fr_1fr] auto-rows-min gap-y-1 pb-2 pointer-events-none z-10">
                        <div className="col-start-1 row-start-1 h-8"></div>
                        {week.map((day, dayIdx) => {
                          const isCurrentMonth = isSameMonth(day, currentDate);
                          const isToday = isSameDay(day, new Date());
                          const isWeekend = dayIdx >= 5;

                          return (
                            <div key={`date-${day.toISOString()}`} className="col-start-auto row-start-1 h-8 p-1 flex justify-between items-start pointer-events-auto" onClick={() => openNewEventModal(day)}>
                              <div className="flex items-center">
                                <span className={`text-sm flex items-center justify-center font-bold ${isToday ? 'text-white w-7 h-7 rounded-full' : (isWeekend ? theme.textFaint : theme.textMain)}`} style={{ backgroundColor: isToday ? theme.accent : 'transparent' }}>
                                  {format(day, 'd')}
                                </span>
                                {day.getDate() === 1 && <span className={`text-xs ml-1 font-bold ${theme.textMuted}`}>{format(day, 'MMM', {locale: de})}</span>}
                              </div>
                            </div>
                          );
                        })}

                        {slotted.map(se => (
                          <div
                            key={se.event.id}
                            className="pointer-events-auto mx-1 rounded-sm border border-black/10 px-1.5 py-1 text-xs cursor-pointer flex flex-col justify-center leading-tight overflow-hidden"
                            style={{
                              ...getEventTypeStyle(se.event.type),
                              gridColumnStart: se.colStart,
                              gridColumnEnd: `span ${se.colSpan}`,
                              gridRowStart: se.rowStart
                            }}
                            onClick={(e) => openEditEventModal(se.event, e)}
                            title={`${se.event.title} (${getCourseName(se.event.courseId)})${se.event.startTime ? ` [${se.event.startTime}${se.event.endTime ? `-${se.event.endTime}` : ''}]` : ''}`}
                          >
                            <div className="flex items-baseline justify-between gap-1.5 w-full overflow-hidden">
                              <span className="font-bold truncate text-[13px]">{se.event.title}</span>
                              {se.event.startTime && (
                                <span className="text-[11px] font-semibold shrink-0 opacity-95">
                                  {se.event.startTime}{se.event.endTime ? `–${se.event.endTime}` : ''}
                                </span>
                              )}
                            </div>
                            <span className="opacity-90 text-[10px] truncate w-full">
                              {getCourseName(se.event.courseId)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {isModalOpen && (
        <EventModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSave={handleSaveEvent}
          onDelete={editingEvent ? () => handleDeleteEvent(editingEvent.id) : undefined}
          initialDate={selectedDate}
          initialTime={selectedTime}
          event={editingEvent}
          userCourses={user.courses}
          allCourses={allCourses}
          categories={categories}
          username={user.username}
          preferences={{ ...preferences, darkMode: isDark }}
          isReadOnly={isReadOnly}
          isAdmin={isAdmin}
        />
      )}

      {isSettingsOpen && (
        <SettingsModal
          isOpen={isSettingsOpen}
          onClose={() => setIsSettingsOpen(false)}
          preferences={{ ...preferences, darkMode: isDark }}
          hasPin={user.hasPin}
          initialCourses={user.courses}
          allCourses={allCourses}
          categories={categories}
          defaultTab={isInitialSetup ? 'kurse' : 'allgemein'}
          onSave={onUpdatePreferences}
          username={user.username}
          onCategoriesChanged={async () => { await Promise.all([loadCategories(), loadEvents()]); }}
        />
      )}

      {isAdminOpen && (
        <AdminModal
          isOpen={isAdminOpen}
          onClose={() => {
            setIsAdminOpen(false);
            loadCourses();
            loadCategories();
          }}
          username={user.username}
          preferences={{ ...preferences, darkMode: isDark }}
        />
      )}
    </div>
  );
}
