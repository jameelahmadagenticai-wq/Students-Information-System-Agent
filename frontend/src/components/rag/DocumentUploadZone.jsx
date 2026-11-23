import { useState, useRef } from "react";
import { aiApi } from "../../api/ai";
import { Spinner } from "../ui/Spinner";
import {
  UploadCloud, FileText, CheckCircle2, AlertCircle,
  X, ArrowRight, ShieldCheck, File
} from "lucide-react";
import toast from "react-hot-toast";

const DOC_TYPES = [
  { value: "policy", label: "District Policy" },
  { value: "handbook", label: "Student Handbook" },
  { value: "iep_guideline", label: "IEP / Special Ed Guidelines" },
  { value: "attendance", label: "Attendance Protocol" },
  { value: "general", label: "General Guidelines" },
];

export function DocumentUploadZone({ onUploadSuccess }) {
  const [file, setFile] = useState(null);
  const [docType, setDocType] = useState("policy");
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const fileInputRef = useRef(null);

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const validateAndSetFile = (selectedFile) => {
    if (!selectedFile) return;

    const allowed = ["application/pdf", "text/plain"];
    const ext = selectedFile.name.split(".").pop().toLowerCase();

    if (!allowed.includes(selectedFile.type) && !["pdf", "txt"].includes(ext)) {
      toast.error("Only PDF and TXT documents are supported.");
      return;
    }

    if (selectedFile.size > 10 * 1024 * 1024) {
      toast.error("File size exceeds 10MB limit.");
      return;
    }

    setFile(selectedFile);
    setUploadResult(null);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file || uploading) return;

    setUploading(true);
    setUploadResult(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("doc_type", docType);

    try {
      const response = await aiApi.uploadDocument(formData);
      const data = response.data;

      setUploadResult({
        success: true,
        filename: data.filename || file.name,
        chunks: data.chunks || 0,
        namespace: data.namespace || "default",
      });

      toast.success(`"${file.name}" ingested successfully (${data.chunks} chunks)!`);
      if (onUploadSuccess) {
        onUploadSuccess(data);
      }
      setFile(null);
    } catch (err) {
      const errMsg = err.response?.data?.detail || "Failed to upload document. Please try again.";
      setUploadResult({
        success: false,
        error: typeof errMsg === "string" ? errMsg : JSON.stringify(errMsg),
      });
      toast.error(typeof errMsg === "string" ? errMsg : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="flex flex-col h-full space-y-4 p-4">
      <div className="bg-primary-50/60 border border-primary-100 rounded-xl p-3">
        <div className="flex items-center gap-2 text-primary-800 text-xs font-semibold">
          <ShieldCheck size={14} className="text-primary-600" />
          <span>Pinecone Knowledge Base Ingestion</span>
        </div>
        <p className="text-[11px] text-primary-700/80 mt-1">
          Upload school policies, district handbooks, or IEP guidelines. Documents will be chunked, enriched with headers, and indexed in Pinecone.
        </p>
      </div>

      {/* Document Type Selector */}
      <div>
        <label className="block text-xs font-medium text-text-secondary mb-1">
          Document Classification
        </label>
        <select
          value={docType}
          onChange={(e) => setDocType(e.target.value)}
          disabled={uploading}
          className="w-full text-xs bg-white border border-surface-border rounded-xl px-3 py-2 text-text-primary focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500 transition-all"
        >
          {DOC_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      {/* Drag & Drop Zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !uploading && fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center ${
          isDragging
            ? "border-primary-500 bg-primary-50/50 scale-[1.01]"
            : "border-surface-border hover:border-primary-400 bg-white hover:bg-surface-muted/30"
        } ${uploading ? "opacity-60 pointer-events-none" : ""}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt"
          onChange={handleFileChange}
          className="hidden"
        />

        <div className="w-12 h-12 rounded-2xl bg-primary-100/70 text-primary-600 flex items-center justify-center mb-3 shadow-sm">
          <UploadCloud size={24} />
        </div>

        <p className="text-xs font-semibold text-text-primary">
          Click to upload or drag & drop
        </p>
        <p className="text-[10px] text-text-muted mt-1">
          Supported formats: PDF, TXT (Maximum 10 MB)
        </p>
      </div>

      {/* Selected File Card */}
      {file && (
        <div className="bg-white border border-surface-border rounded-xl p-3 flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-primary-100 flex items-center justify-center text-primary-600 shrink-0">
              <FileText size={16} />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-text-primary truncate">
                {file.name}
              </p>
              <p className="text-[10px] text-text-muted">
                {(file.size / 1024).toFixed(1)} KB
              </p>
            </div>
          </div>
          {!uploading && (
            <button
              onClick={() => setFile(null)}
              className="p-1 rounded-lg text-text-muted hover:text-text-primary hover:bg-surface-muted transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>
      )}

      {/* Action Button */}
      <button
        onClick={handleUpload}
        disabled={!file || uploading}
        className={`w-full py-2.5 px-4 rounded-xl text-xs font-semibold flex items-center justify-center gap-2 transition-all shadow-sm ${
          !file || uploading
            ? "bg-surface-muted text-text-muted cursor-not-allowed border border-surface-border"
            : "bg-primary-600 hover:bg-primary-700 text-white shadow-primary-500/20"
        }`}
      >
        {uploading ? (
          <>
            <Spinner className="w-3.5 h-3.5 text-white" />
            <span>Chunking & Indexing in Pinecone...</span>
          </>
        ) : (
          <>
            <UploadCloud size={14} />
            <span>Upload & Ingest to Pinecone</span>
          </>
        )}
      </button>

      {/* Result feedback */}
      {uploadResult && (
        <div
          className={`rounded-xl p-3 text-xs border ${
            uploadResult.success
              ? "bg-green-50/80 border-green-200 text-green-900"
              : "bg-red-50/80 border-red-200 text-red-900"
          }`}
        >
          {uploadResult.success ? (
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 font-semibold text-green-800">
                <CheckCircle2 size={14} className="text-green-600" />
                <span>Ingestion Successful!</span>
              </div>
              <p className="text-[11px] text-green-700">
                <strong>{uploadResult.filename}</strong> was split into <strong>{uploadResult.chunks} chunks</strong> and indexed into namespace <code className="bg-green-100 px-1 py-0.5 rounded text-[10px]">{uploadResult.namespace}</code>.
              </p>
              <p className="text-[10px] text-green-600/90 pt-1">
                You can now switch to the Chat tab and query questions from this document!
              </p>
            </div>
          ) : (
            <div className="flex items-start gap-1.5 text-red-800">
              <AlertCircle size={14} className="text-red-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Ingestion Failed</p>
                <p className="text-[11px] text-red-700 mt-0.5">{uploadResult.error}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
