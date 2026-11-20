import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "/api/v1";

const client = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem("sis_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

client.interceptors.response.use(
  (res) => res,
  (err) => {
    // Only redirect to login if the FAILED request was NOT the login endpoint itself
    // and the user has no token stored (truly unauthenticated).
    // Do NOT redirect on 401s from optional dashboard data fetches.
    if (err.response?.status === 401) {
      const url = err.config?.url || "";
      const isLoginRequest = url.includes("/auth/login");
      const isAuthError = err.response?.data?.detail === "Invalid or expired token";

      // If token expired or missing, clear storage and redirect to login
      if (!isLoginRequest && (isAuthError || !localStorage.getItem("sis_token"))) {
        localStorage.removeItem("sis_token");
        localStorage.removeItem("sis_refresh");
        localStorage.removeItem("sis_user");
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  }
);

export default client;
