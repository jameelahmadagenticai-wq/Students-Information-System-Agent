import client from "./client";

export const aiApi = {
  query: (question, context = {}) =>
    client.post("/query/ask", { question, context }),
  reports: (type, params = {}) =>
    client.post("/reports/generate", { report_type: type, ...params }),
  forecast: (type, params = {}) =>
    client.post("/forecasts/run", { forecast_type: type, ...params }),
  uploadDocument: (formData) =>
    client.post("/rag/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }),
  getRagStatus: () =>
    client.get("/rag/status"),
};
