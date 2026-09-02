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
        className="bg-white dark:bg-[#1e1e1e] rounded-lg max-w-md w-full p-5 shadow-lg border border-[#cbd5e1] dark:border-[#444] relative text-[#0f172a] dark:text-[#eeeeee] space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
          <div className="flex items-center justify-between border-b border-[#cbd5e1] dark:border-[#444] pb-3">
          <h3 className="font-extrabold text-xl">
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
        <div className="space-y-3.5 text-xs sm:text-sm text-[#475569] dark:text-[#cbd5e1] leading-relaxed font-normal">
          <p>
            Diese Webseite hat keine offizielle Verbindung mit dem Gymnasium Olbernhau und wurde privat von Schülern erstellt.
          </p>
          <p>
            Der Zugriff ist für Schüler:innen der 11. Klasse des Gymnasiums Olbernhau sowie in Ausnahmefällen für weitere autorisierte Schüler:innen vorgesehen.
          </p>
          <p>
            Bei Fragen oder Problemen erreichst du uns unter support@cal11.de.
          </p>
        </div>

        {/* Close Button in Pink Farbschema */}
        <div className="pt-2">
          <button
            onClick={onClose}
            className="w-full bg-[#e91e63] hover:bg-[#d81b60] text-white font-semibold py-2.5 px-4 rounded-md transition-colors text-sm cursor-pointer"
          >
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
}
