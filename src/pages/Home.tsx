import { useState, FormEvent, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { checkUser, registerUser, loginUser, fetchCurrentSession } from '../lib/api';
import { saveStoredSession } from '../lib/auth';
import { Lock, User, ArrowRight, ChevronLeft, AlertCircle } from 'lucide-react';
import AuthFooter from '../components/AuthFooter';
import { VERTRETUNGSPLAN_URL } from '../lib/externalLinks';

export default function Home() {
  const [name, setName] = useState('');
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let disposed = false;
    let checking = false;
    const detectLogin = async () => {
      if (checking || disposed) return;
      checking = true;
      try {
        const session = await fetchCurrentSession();
        if (!disposed && session?.user?.username) {
          navigate(`/${session.user.username}`, { replace: true });
        }
      } catch {
        // Ohne gültige Session bleibt die Anmeldeseite unverändert.
      } finally {
        checking = false;
      }
    };
    void detectLogin();
    const timer = window.setInterval(detectLogin, 3000);
    const onFocus = () => void detectLogin();
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void detectLogin();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [navigate]);

  const handleNext = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setError('');
    setLoading(true);
    try {
      const check = await checkUser(name.trim());
      if (check.exists) {
        if (check.blocked || check.status === 'BLOCKED') {
          setError('Dieses Konto wurde gesperrt.');
          return;
        }
        if (check.requiresPin) {
          setStep(2);
        } else {
          const res = await loginUser(name.trim());
          saveStoredSession(res.user.username, res.sessionToken);
          navigate(`/${res.user.username}`);
        }
      } else {
        setError('Benutzer nicht gefunden. Kontaktiere einen Admin.');
      }
    } catch (err: any) {
      setError(err.message || 'Fehler bei der Verbindung zum Server');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await registerUser(name.trim(), pin);
      saveStoredSession(res.user.username, res.sessionToken);
      navigate(`/${res.user.username}`);
    } catch (err: any) {
      setError(err.message || 'Registrierung fehlgeschlagen');
    } finally {
      setLoading(false);
    }
  };

  const handleLoginWithPin = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await loginUser(name.trim(), pin);
      saveStoredSession(res.user.username, res.sessionToken);
      navigate(`/${res.user.username}`);
    } catch (err: any) {
      setError(err.message || 'Falscher PIN-Code');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-[#0f172a] dark:bg-[#121212] dark:text-[#eeeeee] font-sans flex flex-col items-center justify-between w-full">
      {/* CENTERED LOGIN BLOCK */}
      <div className="my-auto max-w-[480px] w-full px-6 py-8 flex flex-col items-center">
        {/* HEADER */}
        <header className="mb-6 text-center">
          <h1 className="text-3xl sm:text-[32px] font-extrabold tracking-tight mb-2">
            Jahrgangskalender 11
          </h1>
          <p className="text-sm sm:text-[15px] text-[#64748b] dark:text-[#aaaaaa] leading-snug max-w-[320px] mx-auto font-medium">
            Klausuren, Hausaufgaben und Termine auf einen Blick.
          </p>
          <a
            href={VERTRETUNGSPLAN_URL}
            className="mt-3 inline-flex items-center rounded-lg border border-[#cbd5e1] dark:border-[#444] px-3 py-2 text-xs font-semibold hover:border-[#e91e63] hover:text-[#e91e63] transition-colors"
          >
            Zum Vertretungsplan
          </a>
        </header>

        {/* ONE-LINE FORMULAR */}
        <div className="w-full">
          {error && (
            <div className="mb-4 p-3.5 bg-[#fff1f2] dark:bg-[#3f1d24] border border-[#fecdd3] dark:border-[#7f1d3a] text-[#be123c] dark:text-[#fecdd3] text-xs sm:text-sm rounded-xl flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {step === 1 ? (
            <form onSubmit={handleNext} className="w-full">
              <div className="w-full bg-white dark:bg-[#1e1e1e] border-[1.5px] border-[#cbd5e1] dark:border-[#444] focus-within:border-[#e91e63] rounded-xl p-1.5 pl-3.5 flex items-center shadow-none transition-colors">
                <div className="text-[#94a3b8] mr-2.5 shrink-0 flex items-center">
                  <User className="w-[18px] h-[18px]" />
                </div>
                <input
                  type="text"
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="z.B. SophiaM"
                  aria-label="Dein Benutzername"
                  spellCheck={false}
                  autoComplete="username"
                  required
                  autoFocus
                  className="flex-1 border-none bg-transparent text-sm sm:text-[15px] font-medium outline-none min-w-0 placeholder:text-[#94a3b8]"
                />
                <button
                  type="submit"
                  disabled={loading || !name.trim()}
                  className="bg-[#e91e63] hover:bg-[#d81b60] text-white border-none rounded-lg py-2.5 px-4 text-sm font-bold cursor-pointer flex items-center gap-1.5 shrink-0 shadow-none transition-colors disabled:opacity-60"
                >
                  <span>{loading ? '...' : 'Weiter'}</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </form>
          ) : step === 2 ? (
            <form onSubmit={handleLoginWithPin} className="w-full space-y-3">
              <div className="w-full bg-white dark:bg-[#1e1e1e] border-[1.5px] border-[#cbd5e1] dark:border-[#444] focus-within:border-[#e91e63] rounded-xl p-1.5 pl-3.5 flex items-center shadow-none transition-colors">
                <div className="text-[#94a3b8] mr-2.5 shrink-0 flex items-center">
                  <Lock className="w-[18px] h-[18px]" />
                </div>
                <input
                  type="password"
                  id="pin"
                  maxLength={4}
                  value={pin}
                  onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder={`PIN für ${name}`}
                  aria-label="Dein 4-stelliger PIN"
                  required
                  autoFocus
                  className="flex-1 border-none bg-transparent text-sm sm:text-[15px] font-medium outline-none min-w-0 placeholder:text-[#94a3b8] tracking-widest"
                />
                <button
                  type="submit"
                  disabled={loading || pin.length < 4}
                  className="bg-[#e91e63] hover:bg-[#d81b60] text-white border-none rounded-lg py-2.5 px-4 text-sm font-bold cursor-pointer flex items-center gap-1.5 shrink-0 shadow-none transition-colors disabled:opacity-60"
                >
                  <span>{loading ? '...' : 'Entsperren'}</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
              <div className="text-center">
                <button
                  type="button"
                  onClick={() => {
                    setStep(1);
                    setPin('');
                    setError('');
                  }}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-[#64748b] dark:text-[#aaa] hover:text-[#e91e63] transition-colors py-1 cursor-pointer"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  <span>Anderer Benutzer</span>
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="w-full space-y-3">
              <div className="w-full bg-white dark:bg-zinc-950 border border-zinc-300 dark:border-zinc-700 focus-within:border-teal-700 rounded-md p-1 pl-3 flex items-center transition-colors">
                <div className="text-[#94a3b8] mr-2.5 shrink-0 flex items-center">
                  <Lock className="w-[18px] h-[18px]" />
                </div>
                <input
                  type="password"
                  id="pin"
                  maxLength={4}
                  value={pin}
                  onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="4-stelliger PIN (optional)"
                  aria-label="4-stelliger PIN (optional)"
                  autoFocus
                  className="flex-1 border-none bg-transparent text-sm sm:text-[15px] font-medium text-[#0f172a] outline-none min-w-0 placeholder:text-[#94a3b8] tracking-widest"
                />
                <button
                  type="submit"
                  disabled={loading || (pin.length > 0 && pin.length < 4)}
                  className="bg-teal-700 hover:bg-teal-800 text-white border-none rounded-md py-2 px-3 text-sm font-semibold cursor-pointer flex items-center gap-1.5 shrink-0 transition-colors disabled:opacity-50"
                >
                  <span>{loading ? '...' : 'Erstellen'}</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
              <div className="text-center">
                <button
                  type="button"
                  onClick={() => {
                    setStep(1);
                    setPin('');
                    setError('');
                  }}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-500 hover:text-teal-700 transition-colors py-1 cursor-pointer"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  <span>Zurück</span>
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      {/* SCHLICHTE FUßZEILE ÜBER DIE GESAMTE BREITE */}
      <AuthFooter />
    </div>
  );
}
