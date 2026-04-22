import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface Publisher {
  id: number;
  name: string;
  display_name: string | null;
  description: string | null;
  logo_url: string | null;
  contact_email: string | null;
  status: 'active' | 'inactive' | 'suspended';
  created_at: string;
  updated_at: string;
}

export interface PublisherCreate {
  name: string;
  display_name?: string;
  description?: string;
  logo_url?: string;
  contact_email?: string;
  status?: string;
}

export interface PublisherUpdate {
  name?: string;
  display_name?: string;
  description?: string;
  logo_url?: string;
  contact_email?: string;
  status?: string;
}

export interface PublisherBook {
  id: number;
  publisher_id: number;
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
  book_type?: 'standard' | 'pdf';
  child_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface PublisherListResponse {
  items: Publisher[];
  total: number;
}

/**
 * Fetch all publishers.
 */
export const fetchPublishers = async (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher[]> => {
  const response = await client.get<PublisherListResponse>('/publishers/', {
    headers: buildAuthHeaders(token, tokenType),
  });
  return response.items;
};

/**
 * Fetch a single publisher by ID.
 */
export const fetchPublisher = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.get<Publisher>(`/publishers/${id}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Fetch a single publisher by name.
 */
export const fetchPublisherByName = (
  name: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.get<Publisher>(`/publishers/by-name/${encodeURIComponent(name)}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Create a new publisher.
 */
export const createPublisher = (
  data: PublisherCreate,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.post<Publisher, PublisherCreate>('/publishers/', data, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Update an existing publisher.
 */
export const updatePublisher = (
  id: number,
  data: PublisherUpdate,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.request<Publisher>(`/publishers/${id}`, {
    method: 'PUT',
    headers: {
      ...buildAuthHeaders(token, tokenType),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

/**
 * Soft-delete a publisher (moves to trash).
 */
export const deletePublisher = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.delete<Publisher>(`/publishers/${id}`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Fetch trashed publishers.
 */
export const fetchTrashedPublishers = async (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher[]> => {
  const response = await client.get<PublisherListResponse>(
    '/publishers/trash',
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );
  return response.items;
};

/**
 * Restore a publisher from trash.
 */
export const restorePublisher = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Publisher> =>
  client.post<Publisher, undefined>(`/publishers/${id}/restore`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Permanently delete a publisher from trash.
 */
export const permanentDeletePublisher = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<void> =>
  client.delete<void>(`/publishers/${id}/permanent`, undefined, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Fetch all books for a specific publisher.
 */
export const fetchPublisherBooks = (
  id: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<PublisherBook[]> =>
  client.get<PublisherBook[]>(`/publishers/${id}/books`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export interface AssetFileInfo {
  name: string;
  path: string;
  size: number;
  content_type: string;
  last_modified: string | null;
}

export interface AssetTypeInfo {
  name: string;
  file_count: number;
  total_size: number;
}

export interface PublisherAssetsResponse {
  publisher_id: number;
  publisher_name: string;
  asset_types: AssetTypeInfo[];
}

/**
 * Fetch all asset types for a publisher.
 */
export const fetchPublisherAssets = (
  publisherId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<PublisherAssetsResponse> =>
  client.get<PublisherAssetsResponse>(`/publishers/${publisherId}/assets`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Fetch files within a specific asset type for a publisher.
 */
export const fetchPublisherAssetFiles = (
  publisherId: number,
  assetType: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AssetFileInfo[]> =>
  client.get<AssetFileInfo[]>(
    `/publishers/${publisherId}/assets/${assetType}`,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

/**
 * Upload an asset file to a publisher's asset folder.
 */
export const uploadPublisherAsset = async (
  publisherId: number,
  assetType: string,
  file: File,
  token: string,
  tokenType: string = 'Bearer'
): Promise<AssetFileInfo> => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(
    `${import.meta.env.VITE_API_BASE_URL || ''}/publishers/${publisherId}/assets/${assetType}`,
    {
      method: 'POST',
      headers: {
        Authorization: `${tokenType} ${token}`,
      },
      body: formData,
    }
  );

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(
      errorBody.detail || `Upload failed with status ${response.status}`
    );
  }

  return response.json();
};
