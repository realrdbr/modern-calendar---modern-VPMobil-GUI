import type { EventCategory } from '../types';

export function mergeCategoriesForDisplay(
  globalCategories: EventCategory[],
  privateCategories: EventCategory[] = [],
  includePrivate = true,
): EventCategory[] {
  const merged = [...globalCategories];
  if (!includePrivate) return merged;

  for (const category of privateCategories) {
    if (!merged.some(entry => entry.id === category.id)) {
      merged.push(category);
    }
  }

  return merged;
}
