import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';
import type { BookRecord } from './books';

export interface BookGroup {
  id: number;
  name: string;
  publisher_id: number;
  book_count: number;
  created_at: string;
  updated_at: string;
}

export interface BookGroupWithBooks extends BookGroup {
  books: BookRecord[];
}

export interface BookGroupListResponse {
  groups: BookGroup[];
}

export const listBookGroups = (
  token: string,
  tokenType: string = 'Bearer',
  publisherId?: number,
  client: ApiClient = apiClient
): Promise<BookGroupListResponse> => {
  const qs = publisherId !== undefined ? `?publisher_id=${publisherId}` : '';
  return client.get<BookGroupListResponse>(`/book-groups${qs}`, {
    headers: buildAuthHeaders(token, tokenType),
  });
};

export const getBookGroup = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookGroupWithBooks> =>
  client.get<BookGroupWithBooks>(`/book-groups/${id}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export const createBookGroup = (
  name: string,
  publisherId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookGroup> =>
  client.post<BookGroup>(
    '/book-groups',
    { name, publisher_id: publisherId },
    { headers: buildAuthHeaders(token, tokenType) }
  );

export const updateBookGroup = (
  id: number,
  name: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookGroup> =>
  client.put<BookGroup>(
    `/book-groups/${id}`,
    { name },
    { headers: buildAuthHeaders(token, tokenType) }
  );

export const deleteBookGroup = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<void> =>
  client.delete<void>(`/book-groups/${id}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

export const addBooksToGroup = (
  id: number,
  bookIds: number[],
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookGroupWithBooks> =>
  client.post<BookGroupWithBooks>(
    `/book-groups/${id}/books`,
    { book_ids: bookIds },
    { headers: buildAuthHeaders(token, tokenType) }
  );

export const removeBookFromGroup = (
  id: number,
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<void> =>
  client.delete<void>(`/book-groups/${id}/books/${bookId}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });
