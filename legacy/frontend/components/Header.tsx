"use client";

import Link from "next/link";
import { Home, Upload, MessageSquare, Database } from "lucide-react";

export function Header() {
  return (
    <header className="bg-white/10 backdrop-blur-lg border-b border-white/20">
      <div className="container mx-auto px-4 py-4">
        <nav className="flex items-center justify-between">
          <Link href="/" className="text-2xl font-bold text-white">
            KB RAG
          </Link>
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="flex items-center gap-2 text-white/80 hover:text-white transition-colors"
            >
              <Home className="w-4 h-4" />
              Home
            </Link>
            <Link
              href="/upload"
              className="flex items-center gap-2 text-white/80 hover:text-white transition-colors"
            >
              <Upload className="w-4 h-4" />
              Upload
            </Link>
            <Link
              href="/chat"
              className="flex items-center gap-2 text-white/80 hover:text-white transition-colors"
            >
              <MessageSquare className="w-4 h-4" />
              Chat
            </Link>
            <Link
              href="/files"
              className="flex items-center gap-2 text-white/80 hover:text-white transition-colors"
            >
              <Database className="w-4 h-4" />
              Files
            </Link>
          </div>
        </nav>
      </div>
    </header>
  );
}

