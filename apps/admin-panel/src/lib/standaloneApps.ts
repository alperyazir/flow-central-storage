import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface TemplateInfo {
  platform: string;
  file_name: string;
  file_size: number;
  uploaded_at: string;
  download_url: string;
  version?: string | null;
}

export interface TemplateListResponse {
  templates: TemplateInfo[];
}

export interface TemplateUploadUrlResponse {
  url: string;
  object_name: string;
  expires_in_seconds: number;
  max_bytes: number;
}

export interface TemplateUploadResponse {
  platform: string;
  file_name: string;
  file_size: number;
  version?: string | null;
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
  version?: string | null;
  stale?: boolean | null;
}

export interface BundleListResponse {
  bundles: BundleInfo[];
}

export interface BundleReconcileResult {
  created: number;
  updated: number;
  removed: number;
  total: number;
}

export interface IncompleteUploadsCleanResult {
  aborted: number;
  names: string[];
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
 * Upload a standalone app template straight to R2, reporting real progress.
 *
 * The bytes bypass our API entirely. Cloudflare refuses any request body over
 * 100 MB before it reaches the origin, and the win and mac templates are
 * larger than that, so an upload routed through the API was rejected at the
 * edge after several silent minutes. The server hands out a presigned URL,
 * the browser PUTs to it, and a second call records the result.
 *
 * XHR rather than fetch, because fetch reports no upload progress at all -
 * which is why the bar used to sit at 50% for the whole upload.
 */
export const uploadTemplate = async (
  platform: string,
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  version?: string,
  client: ApiClient = apiClient,
  onProgress?: (progress: number) => void
): Promise<TemplateUploadResponse> => {
  const headers = buildAuthHeaders(token, tokenType);
  const trimmed = version?.trim();

  const target = await client.post<TemplateUploadUrlResponse>(
    `/standalone-apps/${platform}/upload-url`,
    { file_name: file.name, file_size: file.size },
    { headers }
  );

  await putWithProgress(target.url, file, onProgress);

  return client.post<TemplateUploadResponse>(
    `/standalone-apps/${platform}/upload-complete`,
    { file_name: file.name, version: trimmed || null },
    { headers }
  );
};

/** PUT a file to a presigned URL, reporting 0-100 as the bytes go out. */
const putWithProgress = (
  url: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<void> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.setRequestHeader('Content-Type', 'application/zip');

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !onProgress) return;
      onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Upload to storage failed (${xhr.status})`));
    xhr.onerror = () =>
      reject(
        new Error(
          'Upload to storage failed. If this keeps happening, the bucket may be missing its CORS rule.'
        )
      );
    xhr.onabort = () => reject(new Error('Upload cancelled'));
    xhr.send(file);
  });

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
 * Reconcile the bundle index against R2 (three-way diff). Returns counts.
 */
export const reconcileBundles = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BundleReconcileResult> =>
  client.post<BundleReconcileResult>(
    '/standalone-apps/bundles/reconcile',
    undefined,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

/**
 * Abort incomplete multipart uploads left in R2 by killed/stuck bundle builds.
 */
export const cleanIncompleteUploads = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<IncompleteUploadsCleanResult> =>
  client.post<IncompleteUploadsCleanResult>(
    '/standalone-apps/bundles/clean-incomplete',
    undefined,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

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

export interface BundleCoverage {
  /** Platforms that have a template (i.e. are expected to be bundled). */
  expected: StandalonePlatform[];
  /** Keyed by `${publisher_slug}/${book_name}`. */
  byKey: Record<string, { present: string[]; stale: string[] }>;
}

/**
 * Fetch which platforms each book has a bundle for, plus the set of expected
 * platforms (those with a template). Bundles are keyed by
 * `${publisher_slug}/${book_name}` — the same key the storage path uses.
 */
export const fetchBundleCoverage = async (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BundleCoverage> => {
  const [tpls, bundles] = await Promise.all([
    listTemplates(token, tokenType, client),
    listBundles(token, tokenType, client),
  ]);
  const have = new Set(tpls.templates.map((t) => t.platform));
  const expected = STANDALONE_PLATFORMS.filter((p) => have.has(p));

  const byKey: BundleCoverage['byKey'] = {};
  for (const b of bundles.bundles) {
    const key = `${b.publisher_name}/${b.book_name}`;
    const entry = (byKey[key] ??= { present: [], stale: [] });
    if (!entry.present.includes(b.platform)) entry.present.push(b.platform);
    if (b.stale && !entry.stale.includes(b.platform)) entry.stale.push(b.platform);
  }
  return { expected, byKey };
};
