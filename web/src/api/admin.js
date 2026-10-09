// 관리자 API — role admin/root만 200, 그 외 403 (TopBar 링크도 role로 숨김).
import { request } from "./request.js";

// 가입/빌드 통계. 응답: { users: {total, with_app, signups_7d},
//   builds: {total, succeeded, failed, cancelled, last_24h, avg_success_seconds} }
export function getOverview() {
  return request("/admin/overview");
}

// 가입자 목록 — tenant·빌드 집계 포함.
// 응답: [{id, login, email, avatar_url, role, app_name, tenant_id, custom_domain,
//         build_count, last_build_at, created_at}]
export function listUsers() {
  return request("/admin/users");
}

// 노드별 리소스 — kubelet stats/summary 기반.
// 응답: [{name, role, ready, cpu_used_cores, cpu_capacity_cores,
//         memory_used_bytes, memory_capacity_bytes,
//         disk_used_bytes, disk_capacity_bytes, pod_count, error?}]
export function getNodes() {
  return request("/admin/nodes");
}

// 노드 카드 드릴다운 — 그 노드 Pod별 사용량 + limit (limit 없으면 null).
// 응답: [{namespace, name, cpu_used_cores, cpu_limit_cores,
//         memory_used_bytes, memory_limit_bytes, disk_used_bytes, disk_limit_bytes}]
export function getNodePods(name) {
  return request(`/admin/nodes/${encodeURIComponent(name)}/pods`);
}

// "총 빌드" 카드 드릴다운 — 빌드 기록 최신순 100건 (단계별 소요시간 포함).
// 응답: [{build_id, login, seq, app_name, runtime, build_mode, started_at,
//         nixpacks_seconds, buildkit_seconds, total_seconds, status, error}]
export function listBuildRecords() {
  return request("/admin/builds");
}

// 유저 row 드릴다운 — 선택 스택(runtime/DB/Redis/스토리지) + 테넌트 ns Pod 상태.
// 응답: { login, app_name, tenant_id, custom_domain,
//         config: {runtime, db_type, use_redis, use_storage, build_mode, port,
//                  repo_url, branch, status, created_at} | null,
//         pods: [{name, component, phase, ready, restarts, started_at}] }
export function getUserTenant(userId) {
  return request(`/admin/users/${userId}/tenant`);
}

// 앱 개수 등급 목록 — 응답: [{name, max_apps, users}] (max_apps null = 무제한)
export function listTiers() {
  return request("/admin/tiers");
}

// 등급의 앱 수 조절 (root 전용). maxApps: 1 이상의 정수, null이면 무제한.
export function setTierLimit(name, maxApps) {
  return request(`/admin/tiers/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ max_apps: maxApps }),
  });
}

// 유저의 앱 개수 등급 변경 (root 전용)
export function setUserTier(userId, tier) {
  return request(`/admin/users/${userId}/tier`, {
    method: "PUT",
    body: JSON.stringify({ tier }),
  });
}

// 권한(role) 변경 (root 전용). role: "user" | "admin". root는 API로 못 바꿈.
export function setUserRole(userId, role) {
  return request(`/admin/users/${userId}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}
