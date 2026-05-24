"use client";

import { useState } from "react";
import Link from "next/link";
import { FileText, MessageSquare, Upload, Database } from "lucide-react";

export default function Home() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="container mx-auto px-4 py-16">
        <div className="text-center mb-12">
          <h1 className="text-5xl font-bold text-white mb-4">
            Knowledge Base RAG System
          </h1>
          <p className="text-xl text-slate-300">
            Upload documents and ask questions with AI-powered answers
          </p>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
          <Link
            href="/upload"
            className="bg-white/10 backdrop-blur-lg rounded-lg p-6 hover:bg-white/20 transition-all border border-white/20"
          >
            <Upload className="w-8 h-8 text-purple-400 mb-4" />
            <h3 className="text-xl font-semibold text-white mb-2">
              Upload Documents
            </h3>
            <p className="text-slate-300">
              Upload PDF, DOCX, DOC, or TXT files to build your knowledge base
            </p>
          </Link>

          <Link
            href="/chat"
            className="bg-white/10 backdrop-blur-lg rounded-lg p-6 hover:bg-white/20 transition-all border border-white/20"
          >
            <MessageSquare className="w-8 h-8 text-purple-400 mb-4" />
            <h3 className="text-xl font-semibold text-white mb-2">
              Ask Questions
            </h3>
            <p className="text-slate-300">
              Chat with your documents and get AI-powered answers with citations
            </p>
          </Link>

          <Link
            href="/files"
            className="bg-white/10 backdrop-blur-lg rounded-lg p-6 hover:bg-white/20 transition-all border border-white/20"
          >
            <Database className="w-8 h-8 text-purple-400 mb-4" />
            <h3 className="text-xl font-semibold text-white mb-2">
              File Database
            </h3>
            <p className="text-slate-300">
              View and manage all uploaded documents in your knowledge base
            </p>
          </Link>

          <div className="bg-white/10 backdrop-blur-lg rounded-lg p-6 border border-white/20">
            <FileText className="w-8 h-8 text-purple-400 mb-4" />
            <h3 className="text-xl font-semibold text-white mb-2">
              Features
            </h3>
            <ul className="text-slate-300 text-sm space-y-1">
              <li>• Source citations</li>
              <li>• Confidence scores</li>
              <li>• Cloud storage</li>
              <li>• Vector search</li>
            </ul>
          </div>
        </div>

        <div className="text-center">
          <Link
            href="/chat"
            className="inline-block bg-purple-600 hover:bg-purple-700 text-white font-semibold px-8 py-3 rounded-lg transition-colors"
          >
            Get Started
          </Link>
        </div>
      </div>
    </div>
  );
}

