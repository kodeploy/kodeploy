// /auth/* 호출. cookie session 사용 — 모든 호출에 credentials: 'include'.
const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

// GitHub OAuth 시작 — full-page navigation으로 백엔드가 GitHub로 302 redirect 한다.
// (XHR로 호출하면 redirect를 따라가버려 cookie/state 세팅이 깨짐 → 반드시 location 이동.)
export const GITHUB_LOGIN_URL = `${API_BASE}/auth/github/login`;

// private repo 연결 — GitHub App 설치 페이지로. login과 동일하게 full-page navigation으로 호출.
// 설치 후 백엔드 콜백이 installation_id를 저장하고 web으로 돌려보낸다.
export const GITHUB_INSTALL_URL = `${API_BASE}/auth/github/install`;

async function jsonRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401) {
    const err = new Error("로그인이 필요합니다");
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    const err = new Error(`${res.status} ${detail}`);
    err.status = res.status;
    throw err;
  }
  // 본문 비어있을 수 있음 (logout)
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

// 현재 user 조회 — 미로그인이면 null 반환 (401은 throw 대신 swallow)
export async function getMe() {
  try {
    return await jsonRequest("/auth/me");
  } catch (err) {
    if (err.status === 401) return null;
    throw err;
  }
}

// 회원 탈퇴 — 소유한 앱과 개인 데이터를 모두 지우고 계정을 없앤다. confirm에 내 GitHub 아이디를 그대로 보낸다.
export function deleteAccount(confirm) {
  return jsonRequest("/auth/me", { method: "DELETE", body: JSON.stringify({ confirm }) });
}

export function logout() {
  return jsonRequest("/auth/logout", { method: "POST" });
}
