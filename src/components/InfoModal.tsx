interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export default function InfoModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null;

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div 
        className="bg-white rounded-lg max-w-md w-full p-5 shadow-lg border border-zinc-200 relative text-zinc-800 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <h3 className="font-extrabold text-xl text-slate-900">
            Info
          </h3>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-teal-700 font-semibold text-sm px-2 py-0.5 rounded transition-colors cursor-pointer"
            aria-label="Schließen"
          >
            ✕
          </button>
        </div>

        {/* Simple Text Content */}
        <div className="space-y-3.5 text-xs sm:text-sm text-slate-700 leading-relaxed font-normal">
          <p>
            Diese Webseite hat keinerlei offizielle Verbindung zum Gymnasium Olbernhau und wurde privat erstellt.
          </p>
          <p>
            Diese Webseite ist nur für Schülerinnen und Schüler der Klasse 11 vorgesehen und darf von niemand anderem benutzt werden.
          </p>
          <p>
            Bei Fragen oder Problemen bitte an Lennard oder Gustav per LernSax oder WhatsApp wenden.
          </p>
        </div>

        {/* Close Button in Pink Farbschema */}
        <div className="pt-2">
          <button
            onClick={onClose}
            className="w-full bg-teal-700 hover:bg-teal-800 text-white font-semibold py-2.5 px-4 rounded-md transition-colors text-sm cursor-pointer"
          >
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
}
