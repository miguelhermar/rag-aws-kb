"use client";

import { useState } from "react";
import { Send, Bot, User, Loader2 } from "lucide-react";
import { apiClient, QueryResponse, SourceInfo } from "@/lib/api";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: SourceInfo[];
  confidence_score?: number;
  timestamp?: string;
}

export function ChatBox() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [explainLike10, setExplainLike10] = useState(false);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      role: "user",
      content: input,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const response: QueryResponse = await apiClient.query({
        question: input,
        explain_like_10: explainLike10,
      });

      const assistantMessage: Message = {
        role: "assistant",
        content: response.answer,
        sources: response.sources,
        confidence_score: response.confidence_score,
        timestamp: response.timestamp,
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error: any) {
      const errorMessage =
        error.response?.data?.detail || error.message || "An error occurred";
      const errorMsg: Message = {
        role: "assistant",
        content: `Error: ${errorMessage}`,
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-200px)] bg-white dark:bg-gray-900 rounded-lg shadow-lg">
      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 dark:text-gray-400 mt-12">
            <Bot className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>Start a conversation by asking a question about your documents</p>
          </div>
        )}

        {messages.map((message, idx) => (
          <div
            key={idx}
            className={`flex gap-4 ${
              message.role === "user" ? "justify-end" : "justify-start"
            }`}
          >
            {message.role === "assistant" && (
              <div className="w-8 h-8 rounded-full bg-purple-100 dark:bg-purple-900 flex items-center justify-center flex-shrink-0">
                <Bot className="w-5 h-5 text-purple-600 dark:text-purple-400" />
              </div>
            )}

            <div
              className={`max-w-[80%] rounded-lg p-4 ${
                message.role === "user"
                  ? "bg-purple-600 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
              }`}
            >
              <div className="whitespace-pre-wrap">{message.content}</div>

              {message.role === "assistant" && message.confidence_score !== undefined && (
                <div className="mt-3">
                  <ConfidenceBadge score={message.confidence_score} />
                </div>
              )}

              {message.role === "assistant" && message.sources && message.sources.length > 0 && (
                <div className="mt-4 pt-4 border-t border-gray-300 dark:border-gray-700">
                  <p className="text-sm font-semibold mb-2">Sources:</p>
                  <div className="space-y-2">
                    {message.sources.map((source, sourceIdx) => (
                      <div
                        key={sourceIdx}
                        className="text-xs bg-white dark:bg-gray-700 p-2 rounded"
                      >
                        <p className="font-medium">{source.source}</p>
                        <p className="text-gray-600 dark:text-gray-400 mt-1">
                          {source.content_preview}
                        </p>
                        {source.similarity_score !== undefined && (
                          <p className="text-gray-500 dark:text-gray-500 mt-1">
                            Similarity: {(source.similarity_score * 100).toFixed(1)}%
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {message.role === "user" && (
              <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center flex-shrink-0">
                <User className="w-5 h-5 text-gray-600 dark:text-gray-400" />
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex gap-4 justify-start">
            <div className="w-8 h-8 rounded-full bg-purple-100 dark:bg-purple-900 flex items-center justify-center">
              <Bot className="w-5 h-5 text-purple-600 dark:text-purple-400" />
            </div>
            <div className="bg-gray-100 dark:bg-gray-800 rounded-lg p-4">
              <Loader2 className="w-5 h-5 animate-spin text-purple-600" />
            </div>
          </div>
        )}
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center gap-2 mb-2">
          <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
            <input
              type="checkbox"
              checked={explainLike10}
              onChange={(e) => setExplainLike10(e.target.checked)}
              className="rounded"
            />
            Explain like I'm 10
          </label>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
            placeholder="Ask a question about your documents..."
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 bg-white dark:bg-gray-800"
            disabled={loading}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

