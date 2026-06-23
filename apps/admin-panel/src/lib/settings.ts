import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface AppSettings {
  /** Default state of the "auto-create bundles after upload" checkbox. */
  default_auto_bundle: boolean;
  /** Include the source PDF (raw/original.pdf, ~200MB) in bundles. */
  bundle_include_source_pdf: boolean;
}

export type AppSettingsUpdate = Partial<AppSettings>;

/**
 * Fetch all application settings (defaults merged with stored overrides).
 */
export const getSettings = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AppSettings> =>
  client.get<AppSettings>('/settings', {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Update one or more application settings; omitted fields are left unchanged.
 */
export const updateSettings = (
  update: AppSettingsUpdate,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AppSettings> =>
  client.put<AppSettings, AppSettingsUpdate>('/settings', update, {
    headers: buildAuthHeaders(token, tokenType),
  });
