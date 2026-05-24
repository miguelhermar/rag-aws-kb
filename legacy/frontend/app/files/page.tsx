"use client";

import { useEffect, useState } from "react";
import { apiClient, FileInfo } from "@/lib/api";
import Link from "next/link";
import { ArrowLeft, Trash2, FileText, Calendar, Database } from "lucide-react";

export default function FilesPage() {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    loadFiles();
  }, []);

  const loadFiles = async () => {
    try {
      const response = await apiClient.listFiles();
      setFiles(response.files || []);
    } catch (error) {
      console.error("Error loading files:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (fileId: string) => {
    if (!confirm("Are you sure you want to delete this file?")) return;

    setDeleting(fileId);
    try {
      await apiClient.deleteFile(fileId);
      setFiles(files.filter((f) => f.file_id !== fileId));
    } catch (error) {
      console.error("Error deleting file:", error);
      alert("Failed to delete file");
    } finally {
      setDeleting(null);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString();
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="container mx-auto px-4 py-12">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-white/80 hover:text-white mb-8"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Home
        </Link>

        <div className="max-w-6xl mx-auto">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-4xl font-bold text-white mb-2">File Database</h1>
              <p className="text-slate-300">
                View and manage all documents in your knowledge base
              </p>
            </div>
            <Link
              href="/upload"
              className="bg-purple-600 hover:bg-purple-700 text-white font-semibold px-6 py-3 rounded-lg transition-colors"
            >
              Upload New File
            </Link>
          </div>

          {loading ? (
            <div className="text-center text-white py-12">
              <Database className="w-12 h-12 mx-auto mb-4 opacity-50 animate-spin" />
              <p>Loading files...</p>
            </div>
          ) : files.length === 0 ? (
            <div className="bg-white/10 backdrop-blur-lg rounded-lg p-12 text-center border border-white/20">
              <FileText className="w-16 h-16 mx-auto mb-4 text-slate-400" />
              <p className="text-xl text-slate-300 mb-4">No files uploaded yet</p>
              <Link
                href="/upload"
                className="inline-block bg-purple-600 hover:bg-purple-700 text-white font-semibold px-6 py-3 rounded-lg transition-colors"
              >
                Upload Your First File
              </Link>
            </div>
          ) : (
            <div className="grid gap-4">
              {files.map((file) => (
                <div
                  key={file.file_id}
                  className="bg-white/10 backdrop-blur-lg rounded-lg p-6 border border-white/20 hover:bg-white/15 transition-all"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-2">
                        <FileText className="w-5 h-5 text-purple-400" />
                        <h3 className="text-xl font-semibold text-white">{file.filename}</h3>
                        {file.processed && (
                          <span className="px-2 py-1 bg-green-500/20 text-green-400 text-xs rounded">
                            Processed
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-4 text-sm text-slate-300 ml-8">
                        <span>{formatFileSize(file.file_size)}</span>
                        <span className="flex items-center gap-1">
                          <Calendar className="w-4 h-4" />
                          {formatDate(file.uploaded_at)}
                        </span>
                        {file.chunk_count && (
                          <span>{file.chunk_count} chunks</span>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => handleDelete(file.file_id)}
                      disabled={deleting === file.file_id}
                      className="p-2 text-red-400 hover:text-red-300 hover:bg-red-500/20 rounded transition-colors disabled:opacity-50"
                    >
                      <Trash2 className="w-5 h-5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

