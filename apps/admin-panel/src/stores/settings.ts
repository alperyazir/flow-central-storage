import { create } from 'zustand';

import {
  getSettings,
  updateSettings,
  type AppSettings,
  type AppSettingsUpdate,
} from 'lib/settings';

const DEFAULTS: AppSettings = {
  default_auto_bundle: true,
  bundle_include_source_pdf: true,
};

interface SettingsState {
  settings: AppSettings;
  loaded: boolean;
  loading: boolean;
  /** Fetch settings from the API. Falls back to defaults on failure. */
  load: (token: string, tokenType?: string) => Promise<void>;
  /** Persist a partial update and store the returned full settings object. */
  save: (
    update: AppSettingsUpdate,
    token: string,
    tokenType?: string
  ) => Promise<void>;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  settings: DEFAULTS,
  loaded: false,
  loading: false,
  load: async (token, tokenType = 'Bearer') => {
    set({ loading: true });
    try {
      const settings = await getSettings(token, tokenType);
      set({ settings, loaded: true });
    } catch {
      // Keep defaults if settings can't be fetched (e.g. offline / 401).
    } finally {
      set({ loading: false });
    }
  },
  save: async (update, token, tokenType = 'Bearer') => {
    const settings = await updateSettings(update, token, tokenType);
    set({ settings, loaded: true });
  },
}));
