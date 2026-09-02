import { useState } from 'react';
import InfoModal from './InfoModal';
import { KALENDER_URL } from '../lib/externalLinks';

export default function AuthFooter() {
  const [isInfoOpen, setIsInfoOpen] = useState(false);

  return (
    <>
      <footer className="w-full shrink-0 border-t border-[#cbd5e1] dark:border-[#333] py-4 bg-white dark:bg-[#121212]">
        <div className="w-full px-6 sm:px-12 flex items-center justify-between text-[13.5px] font-semibold text-[#0f172a] dark:text-[#eee]">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setIsInfoOpen(true)}
              className="hover:opacity-75 transition-opacity cursor-pointer font-semibold py-1"
            >
              Info
            </button>
          </div>

          <a href={KALENDER_URL} className="font-bold tracking-wide">
            cal11.de
          </a>
        </div>
      </footer>

      <InfoModal isOpen={isInfoOpen} onClose={() => setIsInfoOpen(false)} />
    </>
  );
}
