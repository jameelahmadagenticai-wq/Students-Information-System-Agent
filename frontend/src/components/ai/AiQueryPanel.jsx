import { useState, useRef, useEffect } from "react";
import { aiApi } from "../../api/ai";
import { useAuth } from "../../contexts/AuthContext";
import { cn } from "../../utils/cn";
import { Spinner } from "../ui/Spinner";
import { DocumentUploadZone } from "../rag/DocumentUploadZone";
import {
  Brain, X, Send, ChevronDown, Sparkles,
  User, RotateCcw, Copy, CheckCheck, UploadCloud, MessageSquare,
} from "lucide-react";
import toast from "react-hot-toast";

const SUGGESTED_QUESTIONS = {
  SuperAdmin:      [
    "How many students are enrolled this year?",
    "What is the overall compliance score?",
    "Summarize system health status.",
  ],
  DistrictAdmin:   [
    "What is the attendance rate this week?",
    "Which students have the most absences?",
    "Summarize budget utilization for this year.",
  ],
  Principal:       [
    "Which grade has the lowest attendance?",
    "How many students are below 90% attendance?",
    "List all scheduling conflicts this week.",
  ],
  Teacher:         [
    "Who was absent from my class today?",
    "What is the average grade in my sections?",
    "Which students need academic support?",
  ],
  SpEdCoordinator: [
    "Which IEPs are overdue for review?",
    "List students with IEP deadlines this week.",
    "What are the IDEA compliance risks?",
  ],
  Parent:          [
    "How is Emma doing in her classes?",
    "How many days has Emma been absent?",
    "Are there any upcoming school events?",
  ],
};

function MessageBubble({ msg, onCopy }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    onCopy?.();
  };

  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-primary-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-2.5 items-start">
      <div className="w-7 h-7 rounded-full bg-primary-100 flex items-center justify-center shrink-0 mt-0.5">
        <Brain size={13} className="text-primary-600" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="bg-white border border-surface-border rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-text-primary leading-relaxed shadow-sm">
          {msg.loading ? (
            <div className="flex items-center gap-2 text-text-muted">
              <Spinner className="w-3.5 h-3.5" />
              <span>Thinking...</span>
            </div>
          ) : (
            <>
              {msg.intent && (
                <div className="flex items-center gap-1.5 mb-2">
                  <span className={cn(
                    "text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wider",
                    msg.intent === "hybrid"
                      ? "bg-purple-100 text-purple-700 border border-purple-200"
                      : msg.intent === "rag"
                      ? "bg-blue-100 text-blue-700 border border-blue-200"
                      : "bg-emerald-100 text-emerald-700 border border-emerald-200"
                  )}>
                    {msg.intent === "hybrid" ? "⚡ Hybrid (SQL + Policy)" : msg.intent === "rag" ? "📚 Document Policy" : "📊 Live SQL Database"}
                  </span>
                  {msg.cached && (
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                      ⚡ Cached
                    </span>
                  )}
                </div>
              )}
              <div className="whitespace-pre-wrap">{msg.content}</div>

              {msg.citations && msg.citations.length > 0 && (
                <div className="mt-3 pt-2.5 border-t border-surface-border text-xs">
                  <div className="font-semibold text-text-muted mb-1.5 flex items-center gap-1 text-[11px]">
                    <span>Sources & Citations:</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {msg.citations.map((cite, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-subtle text-[11px] text-text-secondary border border-surface-border"
                      >
                        📄 {cite.source || "Policy Document"} {cite.page ? `(p. ${cite.page})` : ""}
                        {cite.relevance_score ? ` • ${Math.round(cite.relevance_score * 100)}% match` : ""}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        {!msg.loading && (
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 mt-1 ml-1 text-[10px] text-text-muted hover:text-text-secondary transition-colors"
          >
            {copied ? <CheckCheck size={10} /> : <Copy size={10} />}
            {copied ? "Copied" : "Copy"}
          </button>
        )}
      </div>
    </div>
  );
}

export function AiQueryPanel({ isOpen, onClose }) {
  const { user }                      = useAuth();
  const [messages,  setMessages]      = useState([]);
  const [input,     setInput]         = useState("");
  const [loading,   setLoading]       = useState(false);
  const [activeTab, setActiveTab]     = useState("chat");
  const messagesEndRef                = useRef(null);
  const inputRef                      = useRef(null);

  const canUpload = ["SuperAdmin", "DistrictAdmin", "Principal", "SpEdCoordinator"].includes(user?.role);
  const suggestions = SUGGESTED_QUESTIONS[user?.role] || SUGGESTED_QUESTIONS.SuperAdmin;

  useEffect(() => {
    if (isOpen && messages.length === 0) {
      setMessages([{
        id:      Date.now(),
        role:    "assistant",
        content: `Hello! I'm your AI assistant for the SIS portal. I can help you query student data, generate reports, and answer questions about ${user?.role === "Parent" ? "your child's" : "your school's"} information.\n\nWhat would you like to know?`,
      }]);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async (text) => {
    const question = (text || input).trim();
    if (!question || loading) return;

    setInput("");
    const userMsg  = { id: Date.now(),     role: "user",      content: question };
    const thinkMsg = { id: Date.now() + 1, role: "assistant", content: "", loading: true };

    setMessages(prev => [...prev, userMsg, thinkMsg]);
    setLoading(true);

    try {
      const { data } = await aiApi.query(question, {
        role:      user?.role,
        tenant_id: user?.tenant_id,
      });

      const answer =
        data?.answer ||
        data?.response ||
        data?.summary ||
        data?.result ||
        (typeof data === "string" ? data : JSON.stringify(data, null, 2));

      setMessages(prev =>
        prev.map(m => m.id === thinkMsg.id
          ? {
              ...m,
              content: answer,
              intent: data?.intent,
              cached: data?.cached,
              citations: data?.citations,
              loading: false,
            }
          : m
        )
      );
    } catch (err) {
      const errMsg = err.response?.data?.detail || "Sorry, I could not process that query. Please try again.";
      setMessages(prev =>
        prev.map(m => m.id === thinkMsg.id
          ? { ...m, content: typeof errMsg === "string" ? errMsg : "An error occurred.", loading: false }
          : m
        )
      );
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    setMessages([{
      id:      Date.now(),
      role:    "assistant",
      content: "Chat cleared. How can I help you?",
    }]);
  };

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 z-40 lg:hidden"
        onClick={onClose}
      />

      {/* Panel */}
      <div className={cn(
        "fixed right-0 top-0 h-full w-full sm:w-96 bg-white z-50",
        "flex flex-col shadow-2xl border-l border-surface-border",
        "transition-transform duration-300"
      )}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3.5 border-b border-surface-border bg-white">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-primary-100 flex items-center justify-center">
              <Brain size={15} className="text-primary-600" />
            </div>
            <div>
              <p className="text-sm font-semibold text-text-primary">AI Assistant</p>
              <p className="text-[10px] text-text-muted">Powered by Google Gemini</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {activeTab === "chat" && (
              <button
                onClick={clearChat}
                className="p-2 rounded-lg text-text-muted hover:text-text-secondary hover:bg-surface-muted transition-colors"
                title="Clear chat"
              >
                <RotateCcw size={14} />
              </button>
            )}
            <button
              onClick={onClose}
              className="p-2 rounded-lg text-text-muted hover:text-text-secondary hover:bg-surface-muted transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tab navigation for authorized staff */}
        {canUpload && (
          <div className="flex border-b border-surface-border bg-surface-muted/30 px-3 pt-2 gap-1">
            <button
              onClick={() => setActiveTab("chat")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-t-lg transition-all",
                activeTab === "chat"
                  ? "bg-white text-primary-600 border-t-2 border-primary-600 shadow-sm"
                  : "text-text-muted hover:text-text-secondary hover:bg-white/50"
              )}
            >
              <MessageSquare size={13} />
              <span>Ask AI</span>
            </button>
            <button
              onClick={() => setActiveTab("upload")}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-t-lg transition-all",
                activeTab === "upload"
                  ? "bg-white text-primary-600 border-t-2 border-primary-600 shadow-sm"
                  : "text-text-muted hover:text-text-secondary hover:bg-white/50"
              )}
            >
              <UploadCloud size={13} />
              <span>Upload Policy</span>
            </button>
          </div>
        )}

        {/* Content Body */}
        {activeTab === "upload" ? (
          <div className="flex-1 overflow-y-auto bg-surface">
            <DocumentUploadZone
              onUploadSuccess={(res) => {
                // Add notification in chat that new document is ready
                setMessages((prev) => [
                  ...prev,
                  {
                    id: Date.now(),
                    role: "assistant",
                    content: `New document "${res.filename}" has been indexed into Pinecone (${res.chunks} chunks). You can now ask me any questions about it!`,
                  },
                ]);
              }}
            />
          </div>
        ) : (
          <>
            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-surface">
              {messages.map((msg) => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Suggested questions */}
            {messages.length <= 1 && (
              <div className="px-4 py-3 border-t border-surface-border bg-white">
                <p className="text-[10px] font-semibold text-text-muted uppercase tracking-wider mb-2 flex items-center gap-1">
                  <Sparkles size={10} /> Suggested questions
                </p>
                <div className="space-y-1.5">
                  {suggestions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => sendMessage(q)}
                      className="w-full text-left text-xs px-3 py-2 rounded-xl bg-surface
                                 hover:bg-primary-50 hover:text-primary-700 text-text-secondary
                                 border border-surface-border hover:border-primary-200
                                 transition-all truncate"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Input */}
            <div className="px-4 py-3 border-t border-surface-border bg-white">
              <div className="flex gap-2 items-end">
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask anything about your data or policies..."
                  disabled={loading}
                  className="flex-1 px-3.5 py-2.5 bg-surface border border-surface-border rounded-xl
                             text-sm focus:outline-none focus:border-primary-400 focus:ring-2
                             focus:ring-primary-100 resize-none placeholder-gray-400 disabled:opacity-60"
                  style={{ color: "#111827", maxHeight: "100px" }}
                />
                <button
                  onClick={() => sendMessage()}
                  disabled={!input.trim() || loading}
                  className="w-10 h-10 rounded-xl bg-primary-600 hover:bg-primary-700
                             flex items-center justify-center shrink-0 transition-all
                             disabled:opacity-50 disabled:cursor-not-allowed active:scale-95"
                >
                  {loading
                    ? <Spinner className="w-4 h-4 border-white/30 border-t-white" />
                    : <Send size={15} className="text-white" />
                  }
                </button>
              </div>
              <p className="text-[9px] text-text-muted mt-1.5 text-center">
                Press Enter to send · Shift+Enter for new line
              </p>
            </div>
          </>
        )}
      </div>
    </>
  );
}
