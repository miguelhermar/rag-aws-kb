"use client";

import { ChatBox } from "@/components/ChatBox";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function ChatPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="container mx-auto px-4 py-8">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-white/80 hover:text-white mb-6"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Home
        </Link>

        <div className="max-w-5xl mx-auto">
          <h1 className="text-4xl font-bold text-white mb-2">Chat with Your Documents</h1>
          <p className="text-slate-300 mb-8">
            Ask questions about your uploaded documents and get AI-powered answers with citations.
          </p>

          <ChatBox />
        </div>
      </div>
    </div>
  );
}

