# Google Search Console 도메인 소유 확인(kodeploy.com).
# 책장 한켠(covebook.kodeploy.com)의 Google 로그인 브랜딩 인증에 필요하다(2026-11-18). 지우면 소유 확인이 풀린다
resource "cloudflare_dns_record" "google_site_verification" {
  zone_id = var.cloudflare_zone_id
  name    = "@"
  type    = "TXT"
  content = "\"google-site-verification=O5gpNi-pUug2kQvr8RwPQMnOw4tf_4P57btf1zhqdcA\""
  ttl     = 1
  comment = "Google Search Console (managed by Terraform)"
}
