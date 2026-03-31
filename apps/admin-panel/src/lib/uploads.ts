import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';
import type { BookRecord } from './books';

export interface UploadManifestEntry {
  path: string;
  size: number;
}

export interface BookUploadResponse {
  book_id: number;
  files: UploadManifestEntry[];
  version: string;
}

export interface NewBookUploadResponse {
  book: BookRecord;
  files: UploadManifestEntry[];
  version: string;
}

export interface AppUploadResponse {
  platform: string;
  version: string;
  files: UploadManifestEntry[];
}

interface UploadOptions {
  override?: boolean;
  publisherId?: number;
}

const appendQueryParams = (
  path: string,
  options: UploadOptions = {}
): string => {
  const params: string[] = [];
  if (options.override) {
    params.push('override=true');
  }
  if (options.publisherId !== undefined) {
    params.push(`publisher_id=${options.publisherId}`);
  }
  if (params.length === 0) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}${params.join('&')}`;
};

const appendArchive = (formData: FormData, file: File) => {
  formData.append('file', file, file.name);
  return formData;
};

export const uploadBookArchive = async (
  bookId: number,
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: UploadOptions = {}
): Promise<BookUploadResponse> => {
  const formData = appendArchive(new FormData(), file);
  return client.postForm<BookUploadResponse>(
    appendQueryParams(`/books/${bookId}/upload`, options),
    formData,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
};

export const uploadNewBookArchive = async (
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: UploadOptions = {}
): Promise<NewBookUploadResponse> => {
  const formData = appendArchive(new FormData(), file);
  return client.postForm<NewBookUploadResponse>(
    appendQueryParams('/books/upload', options),
    formData,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
};

export const uploadAppArchive = async (
  platform: string,
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: UploadOptions = {}
): Promise<AppUploadResponse> => {
  const normalizedPlatform = platform.toLowerCase();
  const formData = appendArchive(new FormData(), file);
  return client.postForm<AppUploadResponse>(
    appendQueryParams(`/apps/${normalizedPlatform}/upload`, options),
    formData,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
};

// ---------------------------------------------------------------------------
// Async upload with progress tracking
// ---------------------------------------------------------------------------

export interface UploadProgress {
  progress: number;
  step: string;
  detail: string;
  book_id: number | null;
  error: string | null;
}

export type ProgressCallback = (progress: UploadProgress) => void;

/**
 * Upload a book with end-to-end progress tracking.
 *
 * Phase 1 (0-40%): XHR upload to server
 * Phase 2 (40-100%): Server processes ZIP → S3, polled via /upload-status
 */
export const uploadNewBookWithProgress = (
  file: File,
  token: string,
  tokenType: string = 'Bearer',
  onProgress: ProgressCallback,
  options: UploadOptions = {},
  apiBaseUrl: string = ''
): { promise: Promise<UploadProgress>; abort: () => void } => {
  const xhr = new XMLHttpRequest();
  let aborted = false;
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const abort = () => {
    aborted = true;
    xhr.abort();
    if (pollTimer) clearInterval(pollTimer);
  };

  const promise = new Promise<UploadProgress>((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file, file.name);

    let url = `${apiBaseUrl}/books/upload-async`;
    const params: string[] = [];
    if (options.override) params.push('override=true');
    if (options.publisherId !== undefined)
      params.push(`publisher_id=${options.publisherId}`);
    if (params.length) url += `?${params.join('&')}`;

    const authHeader = `${tokenType === 'bearer' ? 'Bearer' : tokenType} ${token}`;

    // Phase 1: XHR upload with progress
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && !aborted) {
        const pct = Math.round((e.loaded / e.total) * 40); // 0-40%
        onProgress({
          progress: pct,
          step: 'uploading',
          detail: `${Math.round(e.loaded / 1024 / 1024)}MB / ${Math.round(e.total / 1024 / 1024)}MB`,
          book_id: null,
          error: null,
        });
      }
    };

    xhr.onload = () => {
      if (aborted) return;
      if (xhr.status !== 202) {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.detail || `Upload failed (${xhr.status})`));
        } catch {
          reject(new Error(`Upload failed (${xhr.status})`));
        }
        return;
      }

      // Phase 2: Poll for server-side progress
      let resp: { job_id: string };
      try {
        resp = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error('Invalid response'));
        return;
      }

      onProgress({
        progress: 40,
        step: 'processing',
        detail: 'Server processing...',
        book_id: null,
        error: null,
      });

      pollTimer = setInterval(async () => {
        if (aborted) {
          if (pollTimer) clearInterval(pollTimer);
          return;
        }
        try {
          const statusResp = await fetch(
            `${apiBaseUrl}/books/upload-status/${resp.job_id}`,
            { headers: { Authorization: authHeader } }
          );
          if (!statusResp.ok) return;
          const status: UploadProgress = await statusResp.json();
          onProgress(status);

          if (status.step === 'completed') {
            if (pollTimer) clearInterval(pollTimer);
            resolve(status);
          } else if (status.step === 'error') {
            if (pollTimer) clearInterval(pollTimer);
            reject(new Error(status.error || 'Upload failed'));
          }
        } catch {
          /* poll error — retry next interval */
        }
      }, 1000);
    };

    xhr.onerror = () => {
      if (!aborted) reject(new Error('Network error'));
    };

    xhr.open('POST', url);
    xhr.setRequestHeader('Authorization', authHeader);
    xhr.send(formData);
  });

  return { promise, abort };
};

export interface BulkUploadResult {
  filename: string;
  success: boolean;
  book_id: number | null;
  book_name: string | null;
  publisher: string | null;
  error: string | null;
}

export interface BulkUploadResponse {
  total: number;
  successful: number;
  failed: number;
  results: BulkUploadResult[];
}

export const uploadBulkBookArchives = async (
  files: File[],
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: UploadOptions = {}
): Promise<BulkUploadResponse> => {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('files', file, file.name);
  });
  return client.postForm<BulkUploadResponse>(
    appendQueryParams('/books/upload-bulk', options),
    formData,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
};
