import { ApiClient, apiClient } from './api';
import { buildAuthHeaders } from './http';

// Main 4 options for UI
export type ProcessingJobType =
  | 'full'
  | 'text_only'
  | 'llm_only'
  | 'audio_only'
  | 'unified'
  | 'analysis_only'
  | 'vocabulary_only';
export type ProcessingStatus =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'partial'
  | 'cancelled';
export type JobPriority = 'high' | 'normal' | 'low';

export interface ProcessingTriggerRequest {
  job_type?: ProcessingJobType;
  priority?: JobPriority;
  admin_override?: boolean;
}

export interface ProcessingJobResponse {
  job_id: string;
  book_id: string;
  publisher_id: string;
  job_type: ProcessingJobType;
  status: ProcessingStatus;
  priority: JobPriority;
  progress: number;
  current_step: string;
  error_message?: string | null;
  retry_count: number;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface ProcessingStatusResponse {
  job_id: string;
  book_id: string;
  status: ProcessingStatus;
  progress: number;
  current_step: string;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface CleanupStatsResponse {
  total_deleted: number;
  text_deleted: number;
  modules_deleted: number;
  audio_deleted: number;
  vocabulary_deleted: number;
  metadata_deleted: number;
  errors: string[];
}

/**
 * Trigger AI processing for a book.
 */
export const triggerProcessing = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  options: ProcessingTriggerRequest = {},
  client: ApiClient = apiClient
): Promise<ProcessingJobResponse> =>
  client.post<ProcessingJobResponse, ProcessingTriggerRequest>(
    `/books/${bookId}/process-ai`,
    options,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Get processing status for a book.
 */
export const getProcessingStatus = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<ProcessingStatusResponse> =>
  client.get<ProcessingStatusResponse>(`/books/${bookId}/process-ai/status`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Delete AI data for a book, optionally triggering reprocessing.
 */
export const deleteAIData = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  reprocess: boolean = false,
  client: ApiClient = apiClient
): Promise<CleanupStatsResponse> =>
  client.delete<CleanupStatsResponse>(
    `/books/${bookId}/ai-data?reprocess=${reprocess}`,
    undefined,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Get status color for display.
 */
export const getStatusColor = (
  status: ProcessingStatus
):
  | 'default'
  | 'primary'
  | 'secondary'
  | 'success'
  | 'error'
  | 'info'
  | 'warning' => {
  switch (status) {
    case 'queued':
      return 'info';
    case 'processing':
      return 'primary';
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    case 'partial':
      return 'warning';
    case 'cancelled':
      return 'default';
    default:
      return 'default';
  }
};

/**
 * Get human-readable status label.
 */
export const getStatusLabel = (status: ProcessingStatus): string => {
  switch (status) {
    case 'queued':
      return 'Queued';
    case 'processing':
      return 'Processing';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    case 'partial':
      return 'Partial';
    case 'cancelled':
      return 'Cancelled';
    default:
      return status;
  }
};

// Extended status type that includes 'not_started'
export type ExtendedProcessingStatus = ProcessingStatus | 'not_started';

export interface BookWithProcessingStatus {
  book_id: number;
  book_name: string;
  book_title: string;
  publisher_id: number;
  publisher_name: string;
  processing_status: ExtendedProcessingStatus;
  progress: number;
  current_step: string | null;
  error_message: string | null;
  job_id: string | null;
  last_processed_at: string | null;
}

export interface BooksWithProcessingStatusResponse {
  books: BookWithProcessingStatus[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProcessingQueueItem {
  job_id: string;
  book_id: number;
  book_name: string;
  book_title: string;
  publisher_name: string;
  status: ProcessingStatus;
  progress: number;
  current_step: string;
  position: number;
  created_at: string;
  started_at: string | null;
}

export interface ProcessingQueueResponse {
  queue: ProcessingQueueItem[];
  total_queued: number;
  total_processing: number;
}

/** Selects books server-side, mirroring the /processing/books query params. */
export interface BulkReprocessFilters {
  status?: ExtendedProcessingStatus;
  publisher?: string;
  search?: string;
}

export interface BulkReprocessRequest {
  /** Hand-picked books. Omit when using `filters`. */
  book_ids?: number[];
  /** Queues every book matching the filter, not just the visible page. */
  filters?: BulkReprocessFilters;
  job_type?: ProcessingJobType;
  priority?: JobPriority;
}

export interface BulkReprocessResponse {
  triggered: number;
  skipped: number;
  /** Books the request selected, which may exceed `triggered` at the cap. */
  matched: number;
  errors: string[];
  job_ids: string[];
}

/**
 * Get list of books with their processing status.
 */
export const getBooksWithProcessingStatus = (
  token: string,
  tokenType: string = 'Bearer',
  params: {
    status?: ExtendedProcessingStatus;
    publisher?: string;
    search?: string;
    page?: number;
    page_size?: number;
  } = {},
  client: ApiClient = apiClient
): Promise<BooksWithProcessingStatusResponse> => {
  const searchParams = new URLSearchParams();
  if (params.status) searchParams.set('status', params.status);
  if (params.publisher) searchParams.set('publisher', params.publisher);
  if (params.search) searchParams.set('search', params.search);
  if (params.page !== undefined)
    searchParams.set('page', params.page.toString());
  if (params.page_size !== undefined)
    searchParams.set('page_size', params.page_size.toString());

  const queryString = searchParams.toString();
  const url = `/processing/books${queryString ? `?${queryString}` : ''}`;

  return client.get<BooksWithProcessingStatusResponse>(url, {
    headers: buildAuthHeaders(token, tokenType),
  });
};

/**
 * Get current processing queue.
 */
export const getProcessingQueue = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<ProcessingQueueResponse> =>
  client.get<ProcessingQueueResponse>('/processing/queue', {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Clear processing error for a book (reset to not_started).
 */
export const clearProcessingError = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<{ message: string }> =>
  client.post<{ message: string }, undefined>(
    `/processing/books/${bookId}/clear-error`,
    undefined,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Cancel a queued processing job. Rejects with 409 once it is running —
 * a started job is left to finish.
 */
export const cancelProcessing = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<{ job_id: string; status: ProcessingStatus }> =>
  client.post<{ job_id: string; status: ProcessingStatus }, undefined>(
    `/processing/books/${bookId}/cancel`,
    undefined,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Bulk reprocess multiple books.
 */
export const bulkReprocess = (
  request: BulkReprocessRequest,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<BulkReprocessResponse> =>
  client.post<BulkReprocessResponse, BulkReprocessRequest>(
    '/processing/bulk-reprocess',
    request,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Get status color for extended status (including not_started).
 */
export const getExtendedStatusColor = (
  status: ExtendedProcessingStatus
):
  | 'default'
  | 'primary'
  | 'secondary'
  | 'success'
  | 'error'
  | 'info'
  | 'warning' => {
  if (status === 'not_started') return 'default';
  return getStatusColor(status as ProcessingStatus);
};

/**
 * Get human-readable label for extended status.
 */
export const getExtendedStatusLabel = (
  status: ExtendedProcessingStatus
): string => {
  if (status === 'not_started') return 'Not Started';
  return getStatusLabel(status as ProcessingStatus);
};

// =============================================================================
// Processing Settings Types and Functions
// =============================================================================

export interface GlobalProcessingSettings {
  ai_auto_process_on_upload: boolean;
  ai_auto_process_skip_existing: boolean;
  llm_primary_provider: string;
  llm_fallback_provider: string;
  tts_primary_provider: string;
  tts_fallback_provider: string;
  queue_max_concurrency: number;
  vocabulary_max_words_per_module: number;
  audio_generation_languages: string;
  audio_generation_concurrency: number;
}

export interface GlobalProcessingSettingsUpdate {
  ai_auto_process_on_upload?: boolean;
  ai_auto_process_skip_existing?: boolean;
  llm_primary_provider?: string;
  llm_fallback_provider?: string;
  tts_primary_provider?: string;
  tts_fallback_provider?: string;
  queue_max_concurrency?: number;
  vocabulary_max_words_per_module?: number;
  audio_generation_languages?: string;
  audio_generation_concurrency?: number;
}

export interface PublisherProcessingSettings {
  publisher_id: number;
  publisher_name: string;
  ai_auto_process_enabled: boolean | null; // null = use global
  ai_processing_priority: 'high' | 'normal' | 'low' | null;
  ai_audio_languages: string | null;
}

export interface PublisherProcessingSettingsUpdate {
  ai_auto_process_enabled?: boolean | null;
  ai_processing_priority?: 'high' | 'normal' | 'low' | null;
  ai_audio_languages?: string | null;
}

/**
 * Get global AI processing settings.
 */
export const getProcessingSettings = (
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<GlobalProcessingSettings> =>
  client.get<GlobalProcessingSettings>('/processing/settings', {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Update global AI processing settings.
 */
export const updateProcessingSettings = (
  settings: GlobalProcessingSettingsUpdate,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<GlobalProcessingSettings> =>
  client.put<GlobalProcessingSettings, GlobalProcessingSettingsUpdate>(
    '/processing/settings',
    settings,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Get AI processing settings for a specific publisher.
 */
export const getPublisherProcessingSettings = (
  publisherId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<PublisherProcessingSettings> =>
  client.get<PublisherProcessingSettings>(
    `/processing/publishers/${publisherId}/settings`,
    { headers: buildAuthHeaders(token, tokenType) }
  );

/**
 * Update AI processing settings for a specific publisher.
 */
export const updatePublisherProcessingSettings = (
  publisherId: number,
  settings: PublisherProcessingSettingsUpdate,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<PublisherProcessingSettings> =>
  client.put<PublisherProcessingSettings, PublisherProcessingSettingsUpdate>(
    `/processing/publishers/${publisherId}/settings`,
    settings,
    { headers: buildAuthHeaders(token, tokenType) }
  );

// =============================================================================
// AI Data Viewer Types and Functions
// =============================================================================

export interface StageResult {
  status: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface AIMetadata {
  book_id: string;
  processing_status: string;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  total_pages: number;
  total_modules: number;
  total_vocabulary: number;
  total_audio_files: number;
  languages: string[];
  primary_language: string;
  difficulty_range: string[];
  stages: Record<string, StageResult>;
  errors: Array<{ stage: string; message: string }>;
}

export interface ModuleSummary {
  module_id: number;
  title: string;
  pages: number[];
  word_count: number;
}

export interface ModuleListResponse {
  book_id: string;
  total_modules: number;
  modules: ModuleSummary[];
}

export interface ModuleDetail {
  module_id: number;
  title: string;
  pages: number[];
  text: string;
  topics: string[];
  grammar_points: string[];
  vocabulary_ids: string[];
  language: string;
  summary: string;
  difficulty: string;
  word_count: number;
  extracted_at: string | null;
}

export interface VocabularyWordAudio {
  word: string | null;
  translation: string | null;
}

export interface VocabularyWord {
  id: string;
  word: string;
  translation: string;
  definition: string;
  part_of_speech: string;
  level: string;
  example: string;
  module_id: number | null;
  module_title: string | null;
  page: number | null;
  audio: VocabularyWordAudio | null;
}

export interface VocabularyResponse {
  book_id: string;
  language: string;
  translation_language: string;
  total_words: number;
  words: VocabularyWord[];
  extracted_at: string | null;
}

/**
 * Get AI metadata for a book.
 */
export const getAIMetadata = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AIMetadata> =>
  client.get<AIMetadata>(`/books/${bookId}/ai-data/metadata`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Get modules list for a book.
 */
export const getAIModules = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<ModuleListResponse> =>
  client.get<ModuleListResponse>(`/books/${bookId}/ai-data/modules`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Get module detail for a book.
 */
export const getAIModuleDetail = (
  bookId: number,
  moduleId: number,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<ModuleDetail> =>
  client.get<ModuleDetail>(`/books/${bookId}/ai-data/modules/${moduleId}`, {
    headers: buildAuthHeaders(token, tokenType),
  });

/**
 * Get vocabulary for a book.
 */
export const getAIVocabulary = (
  bookId: number,
  token: string,
  tokenType: string = 'Bearer',
  params: { module_id?: number; page?: number; page_size?: number } = {},
  client: ApiClient = apiClient
): Promise<VocabularyResponse> => {
  const searchParams = new URLSearchParams();
  if (params.module_id !== undefined)
    searchParams.set('module_id', params.module_id.toString());
  if (params.page !== undefined)
    searchParams.set('page', params.page.toString());
  if (params.page_size !== undefined)
    searchParams.set('page_size', params.page_size.toString());

  const queryString = searchParams.toString();
  const url = `/books/${bookId}/ai-data/vocabulary${queryString ? `?${queryString}` : ''}`;

  return client.get<VocabularyResponse>(url, {
    headers: buildAuthHeaders(token, tokenType),
  });
};

export interface AudioUrlResponse {
  word: string;
  language: string;
  url: string;
  expires_in: number;
}

/**
 * Get presigned URL for vocabulary audio.
 */
export const getVocabularyAudioUrl = (
  bookId: number,
  language: string,
  word: string,
  token: string,
  tokenType: string = 'Bearer',
  client: ApiClient = apiClient
): Promise<AudioUrlResponse> =>
  client.get<AudioUrlResponse>(
    `/books/${bookId}/ai-data/audio/vocabulary/${language}/${encodeURIComponent(word)}.mp3`,
    { headers: buildAuthHeaders(token, tokenType) }
  );
