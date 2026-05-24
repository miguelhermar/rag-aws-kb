"use client";

import { useState, useRef } from "react";
import { Upload, CheckCircle2, AlertCircle } from "lucide-react";
import { apiClient, FileUploadResponse } from "@/lib/api";

interface FileUploaderProps {
  onUploadSuccess?: (file: FileUploadResponse) => void;
  onUploadError?: (error: string) => void;
}

export function FileUploader({
  onUploadSuccess,
  onUploadError,
}: FileUploaderProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{
    type: "success" | "error" | null;
    message: string;
  }>({ type: null, message: "" });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragActive, setIsDragActive] = useState(false);

  const handleFileSelect = async (file: File) => {
    setUploading(true);
    setUploadStatus({ type: null, message: "" });

    try {
      const response = await apiClient.uploadFile(file);
      setUploadStatus({
        type: "success",
        message: `File "${file.name}" uploaded successfully!`,
      });
      onUploadSuccess?.(response);
    } catch (error: any) {
      console.error("Upload error:", error);
      
      let errorMessage = "Upload failed";
      
      if (error.code === "ECONNREFUSED" || error.message?.includes("Network Error")) {
        errorMessage = "Cannot connect to backend server. Please make sure the backend is running on http://127.0.0.1:8000";
      } else if (error.response?.data?.detail) {
        errorMessage = error.response.data.detail;
      } else if (error.message) {
        errorMessage = error.message;
      }
      
      setUploadStatus({
        type: "error",
        message: errorMessage,
      });
      onUploadError?.(errorMessage);
    } finally {
      setUploading(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(false);
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(false);
  };

  return (
    <div className="w-full">
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => !uploading && fileInputRef.current?.click()}
        className={`
          border-2 border-dashed rounded-lg p-8 text-center cursor-pointer
          transition-all duration-200
          ${
            isDragActive
              ? "border-purple-500 bg-purple-50 dark:bg-purple-900/20"
              : "border-gray-300 dark:border-gray-700 hover:border-purple-400"
          }
          ${uploading ? "opacity-50 cursor-not-allowed" : ""}
        `}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.txt"
          onChange={handleFileChange}
          className="hidden"
          disabled={uploading}
        />
        <Upload className="w-12 h-12 mx-auto mb-4 text-gray-400" />
        {isDragActive ? (
          <p className="text-lg font-medium text-purple-600 dark:text-purple-400">
            Drop the file here...
          </p>
        ) : (
          <div>
            <p className="text-lg font-medium mb-2">
              Drag & drop a file here, or click to select
            </p>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Supports: PDF, DOCX, DOC, TXT (Max 50MB)
            </p>
          </div>
        )}
      </div>

      {uploadStatus.type && (
        <div
          className={`mt-4 p-4 rounded-lg flex items-center gap-2 ${
            uploadStatus.type === "success"
              ? "bg-green-50 dark:bg-green-900/20 text-green-800 dark:text-green-400"
              : "bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-400"
          }`}
        >
          {uploadStatus.type === "success" ? (
            <CheckCircle2 className="w-5 h-5" />
          ) : (
            <AlertCircle className="w-5 h-5" />
          )}
          <span>{uploadStatus.message}</span>
        </div>
      )}

      {uploading && (
        <div className="mt-4 text-center">
          <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-purple-600"></div>
          <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
            Uploading and processing...
          </p>
        </div>
      )}
    </div>
  );
}

