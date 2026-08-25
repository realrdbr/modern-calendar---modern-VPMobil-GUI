import { useState, useEffect, FormEvent } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { checkUser, loginUser, loginWithSessionToken, fetchUser, saveUserSettings, logoutCurrentSession } from '../lib/api';
import { getStoredSession, saveStoredSession, clearStoredSession } from '../lib/auth';
import CalendarView from '../components/CalendarView';
import AuthFooter from '../components/AuthFooter';
import { User } from '../types';
import { Lock, ArrowLeft, ArrowRight, AlertCircle } from 'lucide-react';

export default function UserDashboard() {
  const { username } = useParams<{ username: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [isBlocked, setIsBlocked] = useState(false);
  
  // PIN Verification State
  const [needsPin, setNeedsPin] = useState(false);
  const [pinInput, setPinInput] = useState('');
  const [pinError, setPinError] = useState('');
  const [submittingPin, setSubmittingPin] = useState(false);

  useEffect(() => {
    if (!username) {
      navigate('/');
      return;
    }
    verifyAndLoadUser();
  }, [username]);

  const verifyAndLoadUser = async () => {
    if (!username) return;
    setLoading(true);
    setPinError('');
    setIsBlocked(false);

    try {
      // 1. Check if user exists and whether PIN is required or if user is blocked
      const check = await checkUser(username);
      if (!check.exists) {
        navigate('/', { replace: true });
        return;
      }

      if (check.blocked || check.status === 'BLOCKED') {
        clearStoredSession(username);
        setIsBlocked(true);
        setLoading(false);
        return;
      }

      // 2. If no PIN is required, log in directly to receive authenticated session
      if (!check.requiresPin) {
        try {
          const res = await loginUser(username);
          if (res.user?.status === 'BLOCKED') {
            clearStoredSession(username);
            setIsBlocked(true);
            setLoading(false);
            return;
          }
          saveStoredSession(username, res.sessionToken);
          setUser(res.user);
          setNeedsPin(false);
          setLoading(false);
          return;
        } catch (loginErr: any) {
          if (loginErr.message?.includes('gesperrt')) {
            clearStoredSession(username);
            setIsBlocked(true);
            setLoading(false);
            return;
          }
          throw loginErr;
        }
      }

      // 3. User requires a PIN. Check if valid sessionToken stored in localStorage
      const session = getStoredSession(username);

      if (session?.sessionToken) {
        try {
          const res = await loginWithSessionToken(username, session.sessionToken);
          if (res.user?.status === 'BLOCKED') {
            clearStoredSession(username);
            setIsBlocked(true);
            setLoading(false);
            return;
          }
          saveStoredSession(username, res.sessionToken);
          setUser(res.user);
          setNeedsPin(false);
          setLoading(false);
          return;
        } catch (authErr: any) {
          if (authErr.message?.includes('gesperrt')) {
            clearStoredSession(username);
            setIsBlocked(true);
            setLoading(false);
            return;
          }
          // Stored session was invalid/expired
          clearStoredSession(username);
        }
      }

      // 4. If no valid session, show PIN entry screen
      setNeedsPin(true);
    } catch (err: any) {
      console.error('Error verifying user:', err);
      if (err.message?.includes('gesperrt')) {
        setIsBlocked(true);
      } else {
        setPinError('Fehler bei der Verbindung zum Server.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handlePinSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username || pinInput.length < 4) return;

    setPinError('');
    setSubmittingPin(true);
    try {
      const res = await loginUser(username, pinInput);
      if (res.user?.status === 'BLOCKED') {
        clearStoredSession(username);
        setIsBlocked(true);
        return;
      }
      saveStoredSession(username, res.sessionToken);
      setUser(res.user);
      setNeedsPin(false);
    } catch (err: any) {
      if (err.message?.includes('gesperrt')) {
        clearStoredSession(username);
        setIsBlocked(true);
      } else {
        setPinError(err.message || 'Falscher PIN-Code');
      }
    } finally {
      setSubmittingPin(false);
    }
  };

  const handleUpdatePreferences = async (preferences: any, newPin?: string, newCourses?: string[], oldPin?: string) => {
    if (!username) return;
    try {
      const updateData: any = { preferences };
      if (newPin !== undefined) {
        updateData.newPin = newPin;
        updateData.oldPin = oldPin;
      }
      if (newCourses !== undefined) {
        updateData.courses = newCourses;
      }
      const res = await saveUserSettings(username, updateData);
      if (res.sessionToken) {
        saveStoredSession(username, res.sessionToken);
      }
      setUser(res.user || res);
    } catch (error: any) {
      console.error('Failed to save settings:', error);
      alert(error.message || 'Fehler beim Speichern der Einstellungen');
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#fff5f8] text-[#718096] font-sans">
        <div className="w-8 h-8 border-3 border-[#e91e63] border-t-transparent rounded-full animate-spin mb-3"></div>
        <span className="text-sm font-medium">Kalender wird geladen...</span>
      </div>
    );
  }

  // Render blocked account screen
  if (isBlocked || user?.status === 'BLOCKED') {
    return (
      <div className="min-h-screen bg-[#fff5f5] flex flex-col items-center justify-center p-4 sm:p-6 font-sans text-[#2d3748]">
        <div className="max-w-md w-full bg-white rounded-3xl shadow-xl shadow-red-100/50 p-6 sm:p-8 border border-[#fee2e2] text-center space-y-4">
          <div className="w-16 h-16 bg-red-100 text-red-600 rounded-2xl flex items-center justify-center mx-auto shadow-inner">
            <Lock className="w-8 h-8" />
          </div>
          <h2 className="text-2xl font-extrabold text-gray-900 tracking-tight">Konto gesperrt</h2>
          <p className="text-sm text-gray-600 leading-relaxed">
            Dein Account <strong className="text-gray-900">{username}</strong> wurde gesperrt. Du hast keinen Zugriff mehr auf den Kalender.
          </p>
          <div className="pt-2">
            <button
              onClick={() => {
                if (username) clearStoredSession(username);
                navigate('/');
              }}
              className="w-full bg-gray-900 hover:bg-black text-white font-bold py-3.5 px-5 rounded-2xl transition-all text-sm shadow-md"
            >
              Zurück zur Startseite
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render PIN authentication screen if PIN is needed
  if (needsPin) {
    return (
      <div className="min-h-screen bg-white text-[#0f172a] font-sans flex flex-col items-center justify-between w-full">
        {/* CENTERED LOGIN BLOCK */}
        <div className="my-auto max-w-[480px] w-full px-6 py-8 flex flex-col items-center">
          {/* HEADER */}
          <header className="mb-6 text-center">
            <h1 className="text-3xl sm:text-[32px] font-extrabold tracking-tight text-[#0f172a] mb-2">
              Jahrgangskalender 11
            </h1>
            <p className="text-sm sm:text-[15px] text-[#64748b] leading-snug max-w-[320px] mx-auto font-medium">
              Klausuren, Hausaufgaben und Termine auf einen Blick.
            </p>
          </header>

          {/* ONE-LINE FORMULAR */}
          <div className="w-full">
            {pinError && (
              <div className="mb-4 p-3.5 bg-[#fff1f2] border border-[#fecdd3] text-[#be123c] text-xs sm:text-sm rounded-xl flex items-center gap-2.5">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{pinError}</span>
              </div>
            )}

            <form onSubmit={handlePinSubmit} className="w-full space-y-3">
              <div className="w-full bg-white border-[1.5px] border-[#cbd5e1] focus-within:border-[#e91e63] rounded-xl p-1.5 pl-3.5 flex items-center shadow-none transition-colors">
                <div className="text-[#94a3b8] mr-2.5 shrink-0 flex items-center">
                  <Lock className="w-[18px] h-[18px]" />
                </div>
                <input
                  type="password"
                  id="pin"
                  maxLength={4}
                  value={pinInput}
                  onChange={(e) => setPinInput(e.target.value)}
                  placeholder={`PIN für ${username}`}
                  aria-label="4-stelliger PIN"
                  required
                  autoFocus
                  className="flex-1 border-none bg-transparent text-sm sm:text-[15px] font-medium text-[#0f172a] outline-none min-w-0 placeholder:text-[#94a3b8] tracking-widest"
                />
                <button
                  type="submit"
                  disabled={submittingPin || pinInput.length < 4}
                  className="bg-[#e91e63] hover:bg-[#d81b60] text-white border-none rounded-lg py-2.5 px-4 text-sm font-bold cursor-pointer flex items-center gap-1.5 shrink-0 shadow-none transition-colors disabled:opacity-60"
                >
                  <span>{submittingPin ? '...' : 'Entsperren'}</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
              <div className="text-center">
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-[#64748b] hover:text-[#e91e63] transition-colors py-1 cursor-pointer"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  <span>Anderer Account / Startseite</span>
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* SCHLICHTE FUßZEILE ÜBER DIE GESAMTE BREITE */}
        <AuthFooter />
      </div>
    );
  }

  const handleLogout = async () => {
    try {
      await logoutCurrentSession();
    } catch (_e) {}
    if (username) {
      clearStoredSession(username);
    }
    setUser(null);
    navigate('/', { replace: true });
  };

  if (!user) return null;

  return (
    <CalendarView
      user={user}
      onUpdatePreferences={handleUpdatePreferences}
      isInitialSetup={user.courses.length === 0}
      onLogout={handleLogout}
    />
  );
}
