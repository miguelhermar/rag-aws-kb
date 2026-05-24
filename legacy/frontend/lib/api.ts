import axios from "axios";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

export interface FileUploadResponse {
  file_id: string;
  filename: string;
  file_size: number;
  file_type: string;
  s3_key: string;
  status: string;
  message: string;
  uploaded_at: string;
}

export interface QueryRequest {
  question: string;
  explain_like_10?: boolean;
  top_k?: number;
}

export interface SourceInfo {
  source: string;
  chunk_index: number;
  file_path: string;
  content_preview: string;
  similarity_score?: number;
}

export interface ConfidenceBreakdown {
  best_similarity: number;
  avg_similarity: number;
  consistency: number;
  keyword_match: number;
  final_score: number;
}

export interface QueryResponse {
  answer: string;
  sources: SourceInfo[];
  confidence_score: number;
  confidence_breakdown?: ConfidenceBreakdown;
  similarity_scores: number[];
  query: string;
  timestamp: string;
  explain_mode: boolean;
}

export interface FileInfo {
  file_id: string;
  filename: string;
  file_size: number;
  file_type: string;
  s3_key: string;
  uploaded_at: string;
  processed: boolean;
  chunk_count?: number;
}

export const apiClient = {
  // Health check
  health: async () => {
    const response = await api.get("/api/health/");
    return response.data;
  },

  // File upload
  uploadFile: async (file: File): Promise<FileUploadResponse> => {
    const formData = new FormData();
    formData.append("file", file);
    const response = await api.post("/api/upload/", formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
      timeout: 300000, // 5 minutes timeout for large files
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total) {
          const percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          );
          console.log(`Upload progress: ${percentCompleted}%`);
        }
      },
    });
    return response.data;
  },

  // Query
  query: async (request: QueryRequest): Promise<QueryResponse> => {
    const response = await api.post("/api/query/", request);
    return response.data;
  },

  // Files
  listFiles: async (skip = 0, limit = 100) => {
    const response = await api.get("/api/files/", {
      params: { skip, limit },
    });
    return response.data;
  },

  getFile: async (fileId: string): Promise<FileInfo> => {
    const response = await api.get(`/api/files/${fileId}`);
    return response.data;
  },

  deleteFile: async (fileId: string) => {
    const response = await api.delete(`/api/files/${fileId}`);
    return response.data;
  },
};

export default api;

