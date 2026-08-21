import { useState } from 'react';
import InfoModal from './InfoModal';
import { KALENDER_URL, VERTRETUNGSPLAN_URL } from '../lib/externalLinks';

export default function AuthFooter() {
  const [isInfoOpen, setIsInfoOpen] = useState(false);

  return (
    <>
      <footer className="w-full border-t border-[#cbd5e1] py-4 mt-auto bg-white">
        <div className="w-full px-6 sm:px-12 flex items-center justify-between text-[13.5px] font-semibold text-[#0f172a]">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setIsInfoOpen(true)}
              className="text-[#0f172a] hover:opacity-75 transition-opacity cursor-pointer font-semibold py-1"
            >
              Info
            </button>
            <a
              href={VERTRETUNGSPLAN_URL}
              className="text-[#0f172a] hover:opacity-75 transition-opacity font-semibold py-1"
            >
              Vertretungsplan
            </a>
          </div>

          <a href={KALENDER_URL} className="font-bold text-[#0f172a] tracking-wide">
            cal11.de
          </a>
        </div>
      </footer>

      <InfoModal isOpen={isInfoOpen} onClose={() => setIsInfoOpen(false)} />
    </>
  );
}
