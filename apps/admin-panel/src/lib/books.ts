import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export type BookType = 'standard' | 'pdf';

export interface BookRecord {
  id: number;
  publisher_id: number;
  publisher_slug?: string;
  publisher: string; // from relationship property
  book_name: string;
  book_title?: string;
  book_cover?: string;
  activity_count?: number;
  activity_details?: Record<string, number>;
  total_size?: number;
  language: string;
  category?: string;
  status: string;
  parent_book_id?: number | null;
  book_type: BookType;
  child_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface FetchBooksOptions {
  publisherId?: number;
  parentBookId?: number;
  topLevelOnly?: boolean;
}

const buildBooksQuery = (opts: FetchBooksOptions = {}): string => {
  const params = new URLSearchParams();
  if (opts.publisherId !== undefined) params.set('publisher_id', String(opts.publisherId));
  if (opts.parentBookId !== undefined) params.set('parent_book_id', String(opts.parentBookId));
  if (opts.topLevelOnly !== undefined) params.set('top_level_only', opts.topLevelOnly ? 'true' : 'false');
  const qs = params.toString();
  return qs ? `?${qs}` : '';
};

export const fetchBooks = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: FetchBooksOptions = {}
): Promise<BookRecord[]> =>
  client.get<BookRecord[]>(`/books/${buildBooksQuery(options)}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export const fetchBook = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookRecord> =>
  client.get<BookRecord>(`/books/${bookId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface CreateChildBookPayload {
  parent_book_id: number;
  book_name: string;
  book_title?: string;
  language?: string;
  book_type: BookType;
  publisher?: string;
  publisher_id?: number;
}

export const createChildBook = (
  payload: CreateChildBookPayload,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookRecord> =>
  client.post<BookRecord, CreateChildBookPayload>('/books/', payload, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface PdfUrlResponse {
  download_url: string;
  filename: string;
  expires_in_seconds: number;
}

export const getPdfDownloadUrl = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<PdfUrlResponse> =>
  client.get<PdfUrlResponse>(`/books/${bookId}/pdf-url`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface DeleteBookChildInfo {
  book_name: string;
  book_type: BookType;
}

export interface DeleteBookResponse {
  job_id: string;
  status: string;
  book: BookRecord;
  children?: DeleteBookChildInfo[];
}

export interface DeleteProgressResponse {
  progress: number;
  step: string;
  detail: string;
  error: string | null;
}

export const deleteBook = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  deleteBundles: boolean = false,
  client: ApiClient = apiClient
): Promise<DeleteBookResponse> =>
  client.delete<DeleteBookResponse>(
    `/books/${bookId}?delete_bundles=${deleteBundles}`,
    undefined,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

export const getDeleteStatus = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<DeleteProgressResponse> =>
  client.get<DeleteProgressResponse>(`/books/delete-status/${jobId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface SyncR2Response {
  synced: boolean;
  books: {
    created: { id: number; publisher_id: number; book_name: string }[];
    removed: { id: number; publisher_id: number; book_name: string }[];
    r2_count: number;
    db_count: number;
  };
  materials: {
    created: { id: number; teacher_id: number; filename: string }[];
    removed: { id: number; teacher_id: number; filename: string }[];
    r2_count: number;
  };
}

export const syncBooksWithR2 = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<SyncR2Response> =>
  client.post<SyncR2Response, undefined>('/books/sync-r2', undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface DownloadJobResponse {
  job_id: string;
  status: string;
}

export interface DownloadStatusResponse {
  job_id: string;
  progress: number;
  step: string;
  error: string | null;
  detail?: string;
  ready: boolean;
}

export const startBookDownload = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<DownloadJobResponse> =>
  client.post<DownloadJobResponse, undefined>(
    `/books/${bookId}/download`,
    undefined,
    { headers: buildAuthHeaders(token, tokenType) }
  );

export const getDownloadStatus = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<DownloadStatusResponse> =>
  client.get<DownloadStatusResponse>(`/books/download-status/${jobId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });
