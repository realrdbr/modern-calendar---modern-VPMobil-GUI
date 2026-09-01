export interface Course {
  id: string;
  name: string;
  teacher: string;
  type: 'LK' | 'GK' | 'AG';
}

export interface EventCategory {
  id: string;
  name: string;
  color: string;
  sort_order?: number;
  isPrivate?: boolean;
  locked?: boolean;
}

export interface UserPreferences {
  darkMode: boolean;
  themeMode?: 'system' | 'light' | 'dark';
  accentColor: string;
  colorKlausur: string;
  colorHausaufgabe: string;
  colorSonstiges: string;
  colorFerien: string;
  categoryColors?: Record<string, string>;
  forcePinChange?: boolean;
}

export interface User {
  username: string;
  courses: string[];
  hasPin?: boolean;
  preferences: UserPreferences;
  status?: 'ACTIVE' | 'READ_ONLY' | 'BLOCKED' | 'ADMIN';
  isAdmin?: boolean;
}

export type EventType = string;

export interface Attachment {
  id: string;
  filename: string;
  mimeType: string;
  data?: string;
  url?: string;
}

export interface AppEvent {
  id: string;
  title: string;
  date: string;
  endDate?: string;
  startTime?: string;
  endTime?: string;
  courseId: string;
  type: EventType;
  description?: string;
  author: string;
  attachments?: Attachment[];
  deletedAt?: string;
  deletedBy?: string;
  updatedAt?: string;
}

export const COURSES: Course[] = [
  // Leistungskurse (LK)
  { id: 'DE1', name: 'DE1', teacher: 'La', type: 'LK' },
  { id: 'DE2', name: 'DE2', teacher: 'Hän', type: 'LK' },
  { id: 'MA1', name: 'MA1', teacher: 'Kön', type: 'LK' },
  { id: 'MA2', name: 'MA2', teacher: 'Ein', type: 'LK' },
  { id: 'EN1', name: 'EN1', teacher: 'Ost', type: 'LK' },
  { id: 'GE1', name: 'GE1', teacher: 'Kel', type: 'LK' },
  { id: 'PH1', name: 'PH1', teacher: 'Fi', type: 'LK' },

  // Grundkurse (GK)
  { id: 'de1', name: 'de1', teacher: 'SomK', type: 'GK' },
  { id: 'de2', name: 'de2', teacher: 'Els1', type: 'GK' },
  { id: 'en1', name: 'en1', teacher: 'Sei', type: 'GK' },
  { id: 'en21', name: 'en21', teacher: 'Els1', type: 'GK' },
  { id: 'fr1', name: 'fr1', teacher: 'Kau', type: 'GK' },
  { id: 'la1', name: 'la1', teacher: 'Mew', type: 'GK' },
  { id: 'ma1', name: 'ma1', teacher: 'Hof1', type: 'GK' },
  { id: 'ph1', name: 'ph1', teacher: 'Ein', type: 'GK' },
  { id: 'ph2', name: 'ph2', teacher: 'Kön', type: 'GK' },
  { id: 'ast1', name: 'ast1', teacher: 'Ein', type: 'GK' },
  { id: 'ch1', name: 'ch1', teacher: 'Ma', type: 'GK' },
  { id: 'ch2', name: 'ch2', teacher: 'Bu', type: 'GK' },
  { id: 'bio1', name: 'bio1', teacher: 'Bu', type: 'GK' },
  { id: 'bio2', name: 'bio2', teacher: 'Zei', type: 'GK' },
  { id: 'inf1', name: 'inf1', teacher: 'Hei1', type: 'GK' },
  { id: 'inf2', name: 'inf2', teacher: 'Hei1', type: 'GK' },
  { id: 'ge1', name: 'ge1', teacher: 'Lan', type: 'GK' },
  { id: 'ge2', name: 'ge2', teacher: 'Wel', type: 'GK' },
  { id: 'grw1', name: 'grw1', teacher: 'La', type: 'GK' },
  { id: 'grw2', name: 'grw2', teacher: 'Kuna', type: 'GK' },
  { id: 'grw3', name: 'grw3', teacher: 'La', type: 'GK' },
  { id: 'geo1', name: 'geo1', teacher: 'Ret', type: 'GK' },
  { id: 'geo2', name: 'geo2', teacher: 'Sch1', type: 'GK' },
  { id: 'eth1', name: 'eth1', teacher: 'Kel', type: 'GK' },
  { id: 'eth2', name: 'eth2', teacher: 'Bin', type: 'GK' },
  { id: 'ree1', name: 'ree1', teacher: 'Sei', type: 'GK' },
  { id: 'ku1', name: 'ku1', teacher: 'Schu', type: 'GK' },
  { id: 'ku2', name: 'ku2', teacher: 'Schu', type: 'GK' },
  { id: 'mu1', name: 'mu1', teacher: 'Rei', type: 'GK' },
  { id: 'spo1', name: 'spo1', teacher: 'So', type: 'GK' },
  { id: 'spo2', name: 'spo2', teacher: 'Wag', type: 'GK' },
  { id: 'spo3', name: 'spo3', teacher: 'Kl', type: 'GK' },

  // Arbeitsgemeinschaften (AG)
  { id: 'CHO', name: 'Chor', teacher: 'Rei', type: 'AG' },
];
