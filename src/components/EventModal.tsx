import { useState, useEffect, FormEvent, ChangeEvent } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { addDays, differenceInCalendarDays, format } from 'date-fns';
import { AppEvent, Attachment, Course, COURSES as DEFAULT_COURSES, UserPreferences, EventCategory } from '../types';
import { uploadFile } from '../lib/api';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSave: (eventData: any) => void;
  onDelete?: () => void;
  initialDate?: Date | null;
  initialTime?: string;
  event?: AppEvent;
  userCourses: string[];
  allCourses?: Course[];
  categories?: EventCategory[];
  username: string;
  preferences: UserPreferences;
  isReadOnly?: boolean;
  canEdit?: boolean;
  isAdmin?: boolean;
  conflict?: boolean;
  onDismissConflict?: () => void;
  onSaveAsNew?: (eventData: any) => void;
}

export default function EventModal({ isOpen, onClose, onSave, onDelete, initialDate, initialTime, event, userCourses, allCourses = DEFAULT_COURSES, categories = [], username, preferences, isReadOnly = false, canEdit = false, isAdmin = false, conflict = false, onDismissConflict, onSaveAsNew }: Props) {
  // If editing an existing event or read-only, default to view mode.
  const [isViewMode, setIsViewMode] = useState(!!event || isReadOnly);
  
  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [showTime, setShowTime] = useState(false);
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [courseId, setCourseId] = useState('ALLGEMEIN');
  const [type, setType] = useState<string>('SONSTIGES');
  const [description, setDescription] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const isDark = preferences.darkMode;
  const theme = {
    bgApp: isDark ? 'bg-[#1e1e1e]' : 'bg-white',
    bgHeader: isDark ? 'bg-[#2a2a2a]' : 'bg-[#f9f9f9]',
    textMain: isDark ? 'text-[#eee]' : 'text-[#333]',
    textMuted: isDark ? 'text-[#aaa]' : 'text-[#555]',
    border: isDark ? 'border-[#333]' : 'border-[#ccc]',
    inputBg: isDark ? 'bg-[#121212]' : 'bg-white',
    accent: preferences.accentColor
  };

  useEffect(() => {
    if (event) {
      setTitle(event.title);
      setDate(event.date);
      setEndDate(event.endDate || '');
      setStartTime(event.startTime || '');
      setEndTime(event.endTime || '');
      setShowTime(!!(event.startTime || event.endTime));
      setCourseId(event.courseId);
      setType(event.type);
      setDescription(event.description || '');
      setAttachments(event.attachments || []);
      setIsViewMode(true);
    } else if (initialDate) {
      setTitle('');
      setDate(format(initialDate, 'yyyy-MM-dd'));
      setEndDate('');
      setStartTime(initialTime || '');
      setEndTime('');
      setShowTime(!!initialTime);
      setCourseId('ALLGEMEIN');
      setType('SONSTIGES');
      setDescription('');
      setAttachments([]);
      setIsViewMode(false);
    }
  }, [event, initialDate]);

  useEffect(() => {
    if (event || isAdmin || categories.length === 0) return;
    const selectableCategory = categories.find(category => !category.isPrivate && !category.locked)
      || categories.find(category => category.isPrivate);
    const currentCategory = categories.find(category => category.id === type);
    const currentIsSelectable = !!currentCategory
      && (currentCategory.isPrivate || !currentCategory.locked);
    if (!currentIsSelectable && selectableCategory) {
      setType(selectableCategory.id);
      if (selectableCategory.isPrivate) setCourseId('ALLGEMEIN');
    }
  }, [categories, event, isAdmin, type]);

  if (!isOpen) return null;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (isReadOnly) return;
    const normalizedEndDate = endDate && endDate >= date && endDate !== date ? endDate : null;
    const normalizedCourseId = categories.find(category => category.id === type)?.isPrivate ? 'ALLGEMEIN' : courseId;
    onSave({
      title,
      date,
      endDate: normalizedEndDate,
      startTime: showTime && startTime ? startTime : undefined,
      endTime: showTime && endTime ? endTime : undefined,
      courseId: normalizedCourseId,
      type,
      description,
      attachments
    });
  };

  const handleStartDateChange = (nextDate: string) => {
    setDate(previousDate => {
      setEndDate(previousEndDate => {
        if (!previousEndDate || !previousDate) return previousEndDate;
        const duration = Math.max(0, differenceInCalendarDays(
          new Date(`${previousEndDate}T12:00:00`),
          new Date(`${previousDate}T12:00:00`),
        ));
        return format(addDays(new Date(`${nextDate}T12:00:00`), duration), 'yyyy-MM-dd');
      });
      return nextDate;
    });
  };

  const handleFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

    const MAX_FILE_SIZE_MB = 10;
    const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

    Array.from(files).forEach((file: File) => {
      if (file.size > MAX_FILE_SIZE_BYTES) {
        alert(`Die Datei "${file.name}" ist zu groß (${(file.size / (1024 * 1024)).toFixed(1)} MB). Maximal erlaubt sind ${MAX_FILE_SIZE_MB} MB.`);
        return;
      }

      const reader = new FileReader();
      reader.onload = async (event) => {
        const base64 = event.target?.result as string;
        try {
          const privateAttachment = !!categories.find(category => category.id === type)?.isPrivate;
          const uploaded = await uploadFile(file.name, file.type, base64, privateAttachment);
          setAttachments(prev => [...prev, uploaded]);
        } catch (err) {
          console.error('File upload failed:', err);
          alert('Upload der Datei fehlgeschlagen.');
        }
      };
      reader.readAsDataURL(file);
    });

    e.target.value = '';
  };

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  };

  const downloadAttachment = (attachment: Attachment) => {
    const href = attachment.url || attachment.data;
    if (!href) return;
    const a = document.createElement('a');
    a.href = href;
    a.download = attachment.filename;
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const availableCourses = isAdmin 
    ? allCourses 
    : allCourses.filter(c => userCourses.includes(c.id) || (c.id === 'Chor' && userCourses.includes('CHO')) || (c.id === 'CHO' && userCourses.includes('Chor')));

  const getCourseDisplayName = (cId: string) => {
    if (!cId || cId === 'ALLGEMEIN') return 'Allgemein (für alle)';
    if (cId === 'CHO' || cId === 'Chor') return 'Chor (AG)';
    const found = allCourses.find(c => c.id === cId);
    return found ? `${found.name} (${found.teacher})` : cId;
  };

  const selectedCourseDisplay = getCourseDisplayName(courseId);
  const isPrivateCategory = !!categories.find(category => category.id === type)?.isPrivate;

  if (isViewMode) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3">
        <div className={`${theme.bgApp} shadow-lg rounded-lg w-full max-w-md border ${theme.border} text-black flex flex-col max-h-[90vh]`}>
          <div className={`flex items-center justify-between px-4 py-3 border-b ${theme.border} ${theme.bgHeader}`}>
            <h2 className={`text-lg font-bold ${theme.textMain}`}>Termindetails</h2>
            <button onClick={onClose} className={`${theme.textMuted} hover:${theme.textMain} font-bold text-xl leading-none`}>
              &times;
            </button>
          </div>
          
          <div className="p-5 space-y-4 overflow-y-auto">
            <div>
              <h3 className={`text-xl font-bold ${theme.textMain} mb-1 break-words whitespace-normal overflow-hidden`}>{title}</h3>
              <div className={`text-sm ${theme.textMuted} flex flex-wrap items-center gap-2`}>
                <span>{date} {endDate && endDate !== date && `- ${endDate}`}</span>
                {(startTime || endTime) && (
                  <span className={`text-sm font-semibold flex items-center gap-1.5 ${theme.textMain}`}>
                    🕒 {startTime || '--:--'}{endTime ? ` – ${endTime}` : ''} Uhr
                  </span>
                )}
              </div>
            </div>
            
            <div className={`text-sm ${theme.textMain} grid grid-cols-[100px_1fr] gap-2`}>
              <span className={`font-semibold ${theme.textMuted}`}>Typ:</span>
              <span>{categories.find(c => c.id === type)?.name || type}</span>
              
              <span className={`font-semibold ${theme.textMuted}`}>Kurs:</span>
              <span>{selectedCourseDisplay}</span>
            </div>

            {description && (
              <div className={`text-sm ${theme.textMain} mt-4 whitespace-pre-wrap`}>
                <span className={`block font-semibold ${theme.textMuted} mb-1`}>Notizen:</span>
                {description}
              </div>
            )}

            {attachments.length > 0 && (
              <div className="mt-4">
                <span className={`block font-semibold text-sm ${theme.textMuted} mb-2`}>Dateien:</span>
                <div className="space-y-1">
                  {attachments.map(att => (
                    <div key={att.id} className={`flex items-center text-sm p-2 border ${theme.border} ${theme.bgHeader} rounded`}>
                      <span 
                        className={`truncate cursor-pointer hover:underline ${theme.textMain}`} 
                        onClick={() => downloadAttachment(att)}
                      >
                        {att.filename}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className={`p-4 border-t ${theme.border} flex justify-between items-center gap-2`}>
            {isReadOnly && (
              <span className="text-xs font-semibold text-amber-500 bg-amber-500/10 px-2.5 py-1 rounded">
                Nur Lesezugriff
              </span>
            )}
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className={`px-4 py-1.5 text-sm font-semibold ${theme.textMuted} border ${theme.border} hover:opacity-80 transition-colors rounded`}
              >
                Schließen
              </button>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => setIsViewMode(false)}
                  className="px-4 py-1.5 text-sm font-semibold text-white transition-colors rounded"
                  style={{ backgroundColor: theme.accent }}
                >
                  Bearbeiten
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Edit Mode
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3">
      <div className={`${theme.bgApp} shadow-lg rounded-lg w-full max-w-md border ${theme.border} text-black flex flex-col max-h-[90vh]`}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${theme.border} ${theme.bgHeader}`}>
          <h2 className={`text-lg font-bold ${theme.textMain}`}>
            {event ? 'Termin bearbeiten' : 'Neuer Termin'}
          </h2>
          <button onClick={onClose} className={`${theme.textMuted} hover:${theme.textMain} font-bold text-xl leading-none`}>
            &times;
          </button>
        </div>
        
        <form onSubmit={handleSubmit} className="p-4 space-y-4 overflow-y-auto">
          {conflict && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
              <div className="min-w-0 flex-1 leading-relaxed">
                Termin wurde neu gespeichert. Deine Änderungen werden nicht gespeichert, weil die zuerst gespeicherte Version gilt.{" "}
                {onSaveAsNew && <button type="button" onClick={() => onSaveAsNew({ title, date, endDate: endDate || null, startTime: showTime ? startTime : undefined, endTime: showTime ? endTime : undefined, courseId, type, description, attachments })} className="font-semibold underline underline-offset-2">Als neuen Eintrag speichern</button>}
              </div>
              <button type="button" onClick={onDismissConflict} aria-label="Meldung schließen" className="shrink-0 p-0.5 text-base font-bold leading-none" title="Meldung schließen">×</button>
            </div>
          )}
          <div>
            <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Titel</label>
            <input
              type="text"
              required
              value={title}
              onChange={e => setTitle(e.target.value)}
              className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none`}
            />
          </div>
          
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Startdatum</label>
              <input
                type="date"
                required
                value={date}
                onChange={e => handleStartDateChange(e.target.value)}
                className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none`}
              />
            </div>
            <div>
              <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Enddatum (optional)</label>
              <input
                type="date"
                value={endDate}
                min={date}
                onChange={e => setEndDate(e.target.value)}
                className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none`}
              />
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <span className={`text-sm font-semibold ${theme.textMuted}`}>Uhrzeit</span>
              <button
                type="button"
                onClick={() => {
                  const nextState = !showTime;
                  setShowTime(nextState);
                  if (!nextState) {
                    setStartTime('');
                    setEndTime('');
                  }
                }}
                className={`px-3 py-1 text-xs font-bold rounded transition-colors ${
                  showTime 
                    ? 'text-white' 
                    : `${theme.textMuted} border ${theme.border} hover:opacity-80`
                }`}
                style={{ backgroundColor: showTime ? theme.accent : undefined }}
              >
                {showTime ? '✓ Uhrzeit aktiv' : '+ Uhrzeiten angeben'}
              </button>
            </div>

            {showTime && (
              <div className={`grid grid-cols-2 gap-3 p-3 rounded border border-dashed ${theme.border} ${theme.bgHeader}`}>
                <div>
                  <label className={`block text-xs font-semibold ${theme.textMuted} mb-1`}>Von</label>
                  <input
                    type="time"
                    value={startTime}
                    onChange={e => setStartTime(e.target.value)}
                    className={`w-full px-2 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} text-sm focus:outline-none`}
                  />
                </div>
                <div>
                  <label className={`block text-xs font-semibold ${theme.textMuted} mb-1`}>Bis (optional)</label>
                  <input
                    type="time"
                    value={endTime}
                    onChange={e => setEndTime(e.target.value)}
                    className={`w-full px-2 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} text-sm focus:outline-none`}
                  />
                </div>
              </div>
            )}
          </div>
          
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Typ</label>
              <select
                value={type}
                onChange={e => {
                  const nextType = e.target.value;
                  setType(nextType);
                  if (categories.find(category => category.id === nextType)?.isPrivate) setCourseId('ALLGEMEIN');
                }}
                className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none`}
              >
                {categories.map(c => {
                  if (c.locked && !isAdmin) return null;
                  return (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  );
                })}
              </select>
            </div>
            <div>
              <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Kurszuweisung</label>
              <select
                value={courseId}
                onChange={e => setCourseId(e.target.value)}
                disabled={isPrivateCategory}
                aria-describedby={isPrivateCategory ? 'private-course-hint' : undefined}
                className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none text-sm disabled:opacity-45 disabled:cursor-not-allowed`}
              >
                <option value="ALLGEMEIN">Allgemein (für alle)</option>
                {availableCourses.map(c => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.teacher})
                  </option>
                ))}
              </select>
              {isPrivateCategory && <p id="private-course-hint" className={`mt-1 text-[11px] ${theme.textMuted}`}>Private Termine sind keinem Kurs zugeordnet.</p>}
            </div>
          </div>

          <div>
            <label className={`block text-sm font-semibold ${theme.textMuted} mb-1`}>Notizen</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              className={`w-full px-3 py-1.5 border ${theme.border} ${theme.inputBg} ${theme.textMain} focus:outline-none resize-none`}
            />
          </div>
          
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className={`text-sm font-semibold ${theme.textMuted}`}>Dateien hochladen</label>
              <span className={`text-xs ${theme.textMuted}`}>max. 10 MB</span>
            </div>
            <input 
              type="file" 
              multiple 
              onChange={handleFileUpload} 
              className={`w-full text-sm ${theme.textMain} file:mr-2 file:py-1 file:px-2 file:border-0 file:text-sm file:font-semibold file:bg-[${theme.accent}] file:text-white hover:file:opacity-80`} 
              style={{ color: isDark ? '#eee' : '#333' }}
            />
            {attachments.length > 0 && (
              <div className="mt-2 space-y-1">
                {attachments.map(att => (
                  <div key={att.id} className={`flex items-center justify-between text-xs p-1.5 border ${theme.border} ${theme.bgHeader} rounded`}>
                    <span 
                      className={`truncate cursor-pointer hover:underline ${theme.textMain}`} 
                      onClick={() => downloadAttachment(att)}
                    >
                      {att.filename}
                    </span>
                    <button 
                      type="button" 
                      onClick={() => removeAttachment(att.id)}
                      className="text-red-500 hover:text-red-700 ml-2 font-bold"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className={`flex items-center justify-between pt-2 mt-4 border-t ${theme.border}`}>
            {onDelete ? (
              <button
                type="button"
                onClick={() => {
                  if (showDeleteConfirm) {
                    onDelete();
                  } else {
                    setShowDeleteConfirm(true);
                  }
                }}
                className={`text-[#d93025] hover:underline text-sm font-semibold px-2 py-1 -ml-2 rounded ${showDeleteConfirm ? 'bg-red-500/10' : ''}`}
              >
                {showDeleteConfirm ? 'Wirklich löschen?' : 'Löschen'}
              </button>
            ) : <div></div>}
            
            <div className="flex gap-2">
              <button
                type="button"
                onClick={event ? () => setIsViewMode(true) : onClose}
                className={`px-4 py-1.5 text-sm font-semibold ${theme.textMuted} border ${theme.border} hover:opacity-80 transition-colors rounded`}
              >
                Abbrechen
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 text-sm font-semibold text-white transition-colors rounded"
                style={{ backgroundColor: theme.accent }}
              >
                Speichern
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
