import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface BookRecord {
  id: number;
  publisher_id: number;
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
  created_at?: string;
  updated_at?: string;
}

export const fetchBooks = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookRecord[]> =>
  client.get<BookRecord[]>('/books/', {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface DeleteBookResponse {
  job_id: string;
  status: string;
  book: BookRecord;
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
  client: ApiClient = apiClient
): Promise<DeleteBookResponse> =>
  client.delete<DeleteBookResponse>(`/books/${bookId}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

export const getDeleteStatus = (
  jobId: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<DeleteProgressResponse> =>
  client.get<DeleteProgressResponse>(`/books/delete-status/${jobId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });
