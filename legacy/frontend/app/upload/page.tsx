"use client";

import { FileUploader } from "@/components/FileUploader";
import { useState } from "react";
import { FileUploadResponse } from "@/lib/api";
import { useRouter } from "next/navigation";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import Link from "next/link";

export default function UploadPage() {
  const router = useRouter();
  const [uploadedFile, setUploadedFile] = useState<FileUploadResponse | null>(null);

  const handleUploadSuccess = (file: FileUploadResponse) => {
    setUploadedFile(file);
    setTimeout(() => {
      router.push("/files");
    }, 2000);
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

        <div className="max-w-2xl mx-auto">
          <h1 className="text-4xl font-bold text-white mb-4">Upload Document</h1>
          <p className="text-slate-300 mb-8">
            Upload a PDF, DOCX, DOC, or TXT file to add it to your knowledge base.
            The file will be processed and indexed for search.
          </p>

          <div className="bg-white/10 backdrop-blur-lg rounded-lg p-8 border border-white/20">
            <FileUploader
              onUploadSuccess={handleUploadSuccess}
              onUploadError={(error) => console.error("Upload error:", error)}
            />
          </div>

          {uploadedFile && (
            <div className="mt-6 bg-green-500/20 border border-green-500/50 rounded-lg p-4 flex items-center gap-3">
              <CheckCircle2 className="w-6 h-6 text-green-400" />
              <div>
                <p className="text-green-400 font-semibold">
                  File uploaded successfully!
                </p>
                <p className="text-green-300 text-sm">
                  Redirecting to file database...
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

