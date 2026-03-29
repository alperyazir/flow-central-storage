import { ApiClient, apiClient, resolveApiUrl } from './api';
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

const encodePathSegment = (segment: string) => encodeURIComponent(segment);

const bookStorageBasePath = (publisherId: number, bookName: string) =>
  `/storage/books/${publisherId}/${encodePathSegment(bookName)}`;

const buildBookObjectUrl = (
  publisherId: number,
  bookName: string,
  objectPath: string
) =>
  `${bookStorageBasePath(publisherId, bookName)}/object?path=${encodeURIComponent(objectPath)}`;

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

export const listBookContents = (
  publisherId: number,
  bookName: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<StorageNode> =>
  client.get<StorageNode>(bookStorageBasePath(publisherId, bookName), {
    headers: buildAuthHeaders(token, tokenType),
  });

export const fetchBookConfig = (
  publisherId: number,
  bookName: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<Record<string, unknown>> =>
  client.get<Record<string, unknown>>(
    `${bookStorageBasePath(publisherId, bookName)}/config`,
    {
      headers: buildAuthHeaders(token, tokenType),
    }
  );

export interface BookExplorerFetchResult {
  tree: StorageNode | null;
  config: Record<string, unknown> | null;
  treeError: Error | null;
  configError: Error | null;
}

const toError = (reason: unknown): Error => {
  if (reason instanceof Error) {
    return reason;
  }
  if (typeof reason === 'string') {
    return new Error(reason);
  }
  return new Error('Request failed');
};

export const fetchBookExplorerData = async (
  publisherId: number,
  bookName: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BookExplorerFetchResult> => {
  const [treeResult, configResult] = await Promise.allSettled([
    client.get<StorageNode>(bookStorageBasePath(publisherId, bookName), {
      headers: buildAuthHeaders(token, tokenType),
    }),
    client.get<Record<string, unknown>>(
      `${bookStorageBasePath(publisherId, bookName)}/config`,
      {
        headers: buildAuthHeaders(token, tokenType),
      }
    ),
  ]);

  const tree = treeResult.status === 'fulfilled' ? treeResult.value : null;
  const config =
    configResult.status === 'fulfilled' ? configResult.value : null;

  return {
    tree,
    config,
    treeError:
      treeResult.status === 'rejected' ? toError(treeResult.reason) : null,
    configError:
      configResult.status === 'rejected' ? toError(configResult.reason) : null,
  };
};

export const downloadBookObject = async (
  publisherId: number,
  bookName: string,
  objectPath: string,
  token: string,
  tokenType: string = 'Bearer',
  options: DownloadBookObjectOptions = {}
): Promise<Blob> => {
  const { url, init } = createBookObjectRequest(
    publisherId,
    bookName,
    objectPath,
    token,
    tokenType,
    { range: options.range, cache: options.cache }
  );

  const response = await fetch(url, {
    ...init,
    signal: options.signal,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || 'Unable to download file');
  }

  return response.blob();
};

export interface DownloadBookObjectOptions {
  range?: string;
  signal?: AbortSignal;
  cache?: RequestCache;
}

export interface BookObjectRequest {
  url: string;
  init: RequestInit;
}

export interface BookObjectRequestOptions {
  range?: string;
  cache?: RequestCache;
}

export const createBookObjectRequest = (
  publisherId: number,
  bookName: string,
  objectPath: string,
  token: string,
  tokenType: string = 'Bearer',
  options: BookObjectRequestOptions = {}
): BookObjectRequest => {
  const headers: Record<string, string> = {
    ...buildAuthHeaders(token, tokenType),
  };

  if (options.range) {
    headers.Range = options.range;
  }

  return {
    url: resolveApiUrl(buildBookObjectUrl(publisherId, bookName, objectPath)),
    init: {
      method: 'GET',
      headers,
      cache: options.cache ?? 'no-store',
    },
  };
};
