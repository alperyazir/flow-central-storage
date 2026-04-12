import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface TemplateInfo {
  platform: string;
  file_name: string;
  file_size: number;
  uploaded_at: string;
  download_url: string;
}

export interface TemplateListResponse {
  templates: TemplateInfo[];
}

export interface TemplateUploadResponse {
  platform: string;
  file_name: string;
  file_size: number;
  message: string;
}

export interface BundleRequest {
  platform: 'mac' | 'win' | 'win7-8' | 'linux';
  book_id: number;
  force?: boolean;
}

export interface BundleResponse {
  download_url: string;
  file_name: string;
  file_size: number;
  expires_at: string;
}

export interface AsyncBundleRequest {
  platform: 'mac' | 'win' | 'win7-8' | 'linux';
  book_id: number;
  force?: boolean;
}

export interface AsyncBundleResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface BundleJobStatus {
  job_id: string;
  status: string;
  progress: number;
  current_step: string;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  platform: string | null;
  book_name: string | null;
  book_id: string | null;
}

export interface BundleJobListResponse {
  jobs: BundleJobStatus[];
  total: number;
}

export interface BundleJobResult {
  job_id: string;
  status: string;
  progress: number;
  current_step: string;
  download_url: string | null;
  file_name: string | null;
  file_size: number | null;
  cached: boolean;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface BundleInfo {
  publisher_name: string;
  book_name: string;
  platform: string;
  file_name: string;
  file_size: number;
  created_at: string;
  object_name: string;
  download_url: string | null;
}

export interface BundleListResponse {
  bundles: BundleInfo[];
}

export interface TemplateDownloadResponse {
  download_url: string;
  platform: string;
  expires_at: string;
}

/**
 * List all uploaded standalone app templates
 */
export const listTemplates = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<TemplateListResponse> =>
  client.get<TemplateListResponse>('/standalone-apps', {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Upload a standalone app template for a specific platform
 */
export const uploadTemplate = async (
  platform: string,
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<TemplateUploadResponse> => {
  const formData = new FormData();
  formData.append('file', file);

  return client.postForm<TemplateUploadResponse>(
    `/standalone-apps/${platform}/upload`,
    formData,
    { headers: buildAuthHeaders(token, tokenType) }
  );
};

/**
 * Delete a standalone app template for a specific platform
 */
export const deleteTemplate = (
  platform: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<void> =>
  client.delete<void>(`/standalone-apps/${platform}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Get download URL for a standalone app template
 */
export const getTemplateDownloadUrl = (
  platform: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<TemplateDownloadResponse> =>
  client.get<TemplateDownloadResponse>(
    `/standalone-apps/${platform}/download`,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

/**
 * Create a bundled standalone app with book assets (async)
 */
export const createBundle = (
  request: BundleRequest,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AsyncBundleResponse> =>
  client.post<AsyncBundleResponse, BundleRequest>(
    '/standalone-apps/bundle',
    request,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

/**
 * List all created bundles
 */
export const listBundles = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BundleListResponse> =>
  client.get<BundleListResponse>('/standalone-apps/bundles', {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Delete a bundle by its object path
 */
export const deleteBundle = (
  objectName: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<void> =>
  client.delete<void>(`/standalone-apps/bundles/${objectName}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * @deprecated Use createBundle instead — /bundle is now async by default.
 */
export const createBundleAsync = createBundle;

/**
 * Get the status/result of an async bundle creation job
 */
export const getBundleJobStatus = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BundleJobResult> =>
  client.get<BundleJobResult>(`/standalone-apps/bundle-status/${jobId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * List all bundle creation jobs (for progress tracking)
 */
export const listBundleJobs = (
  token: string,
  tokenType: string = 'Bearer',
  statusFilter?: string,
  client: ApiClient = apiClient
): Promise<BundleJobListResponse> => {
  const params = statusFilter ? `?status_filter=${statusFilter}` : '';
  return client.get<BundleJobListResponse>(
    `/standalone-apps/bundle/jobs${params}`,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
};

/**
 * Cancel a bundle job
 */
export const cancelBundleJob = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<{ job_id: string; status: string }> =>
  client.post(`/standalone-apps/bundle/jobs/${jobId}/cancel`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Delete a bundle job record
 */
export const deleteBundleJob = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<{ deleted: boolean; job_id: string }> =>
  client.delete(`/standalone-apps/bundle/jobs/${jobId}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Clear all bundle jobs (optionally by status)
 */
export const clearBundleJobs = (
  token: string,
  tokenType: string = 'Bearer',
  statusFilter?: string,
  client: ApiClient = apiClient
): Promise<{ deleted: number }> => {
  const params = statusFilter ? `?status_filter=${statusFilter}` : '';
  return client.delete(`/standalone-apps/bundle/jobs${params}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });
};

/**
 * Supported platforms for standalone apps
 */
export const STANDALONE_PLATFORMS = ['mac', 'win', 'win7-8', 'linux'] as const;
export type StandalonePlatform = (typeof STANDALONE_PLATFORMS)[number];

/**
 * Human-readable platform labels
 */
export const PLATFORM_LABELS: Record<StandalonePlatform, string> = {
  mac: 'macOS',
  win: 'Windows',
  'win7-8': 'Windows 7/8',
  linux: 'Linux',
};
