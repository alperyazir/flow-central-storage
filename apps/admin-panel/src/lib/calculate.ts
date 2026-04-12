import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface BookStats {
  book_id: number;
  book_name: string;
  total_pages: number;
  no_activity_pages: number;
  activity_types: Record<string, number>;
  total_activities: number;
  games_count: number;
}

export const calculateBooks = (
  publisherId: number,
  bookIds: number[],
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookStats[]> =>
  client.post<BookStats[], { book_ids: number[] }>(
    `/publishers/${publisherId}/calculate`,
    { book_ids: bookIds },
    { headers: buildAuthHeaders(token, tokenType) }
  );
