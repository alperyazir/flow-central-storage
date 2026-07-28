import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

export interface StorageNode {
  path: string;
  type: 'folder' | 'file';
  size?: number;
  children?: StorageNode[];
}

export type TrashItemType = 'book' | 'app' | 'teacher_material' | 'unknown';

export interface TrashEntry {
  key: string;
  bucket: string;
  path: string;
  item_type: TrashItemType;
  object_count: number;
  total_size: number;
  metadata?: {
    publisher?: string;
    Publisher?: string;
    book_name?: string;
    bookName?: string;
    platform?: string;
    Platform?: string;
    version?: string;
    Version?: string;
    teacher_id?: string;
    teacherId?: string;
  };
  youngest_last_modified: string | null;
  eligible_at: string | null;
  eligible_for_deletion: boolean;
}

export interface RestoreResponse {
  restored_key: string;
  objects_moved: number;
  item_type: TrashItemType;
}

export interface TrashDeleteResponse {
  deleted_key: string;
  objects_removed: number;
  item_type: TrashItemType;
}

export interface DeleteTrashOptions {
  force?: boolean;
  overrideReason?: string;
}

export const listAppContents = (
  platform: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<StorageNode> =>
  client.get<StorageNode>(`/storage/apps/${platform}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

export const listTrashEntries = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<TrashEntry[]> =>
  client.get<TrashEntry[]>('/storage/trash', {
    headers: buildAuthHeaders(token, tokenType),
  });

export const restoreTrashEntry = (
  key: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<RestoreResponse> =>
  client.post<RestoreResponse, { key: string }>(
    '/storage/restore',
    { key },
    { headers: buildAuthHeaders(token, tokenType) }
  );

export const deleteTrashEntry = (
  key: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient,
  options: DeleteTrashOptions = {}
): Promise<TrashDeleteResponse> => {
  const payload: {
    key: string;
    force: boolean;
    override_reason?: string;
  } = {
    key,
    force: options.force ?? false,
  };

  if (options.overrideReason) {
    payload.override_reason = options.overrideReason;
  }

  return client.delete<TrashDeleteResponse, typeof payload>(
    '/storage/trash',
    payload,
    { headers: buildAuthHeaders(token, tokenType) }
  );
};
