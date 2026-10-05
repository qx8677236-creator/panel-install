export function apiBaseURL() {
  if (typeof window !== "undefined" && window.location && window.location.host) {
    return window.location.protocol + "//" + window.location.host + "/api"
  }
  return "/api"
}

export function stripApiPrefix(url) {
  if (typeof url === "string" && url.startsWith("/api/")) return url.slice(4)
  return url
}
