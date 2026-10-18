"""Read-only modo infrastructure audit. Output only allowlisted non-personal metadata.

Never exports the token, account/zone identifiers, origin IPs, log destinations,
individual request records or other apps' settings. No mutation API calls.
"""
import datetime
import json
import os
import urllib.error
import urllib.request

HOST = "modo.kodeploy.com"
TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]
ROOT = "https://api.cloudflare.com/client/v4"
report = {"host": HOST, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "read_only": True}

def get(path):
    req = urllib.request.Request(ROOT + path, headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            body = json.load(error)
        except Exception:
            body = {}
        return error.code, body
    except Exception:
        return 0, {}

def status(code, body):
    return {"http_status": code, "success": body.get("success", False), "error_codes": [e.get("code") for e in body.get("errors", []) if isinstance(e, dict)]}

code, body = get("/zones?name=kodeploy.com")
report["zone_access"] = status(code, body)
zones = [z for z in body.get("result", []) if z.get("name") == "kodeploy.com"] if body.get("success") else []
if len(zones) == 1:
    zone = zones[0]
    zone_id = zone["id"]
    report["zone_plan"] = zone.get("plan", {}).get("name")
    report["zone_status"] = zone.get("status")
    for setting in ["ssl", "min_tls_version", "always_use_https", "ip_geolocation"]:
        code, body = get(f"/zones/{zone_id}/settings/{setting}")
        item = status(code, body)
        if body.get("success"):
            item["value"] = body.get("result", {}).get("value")
        report[setting] = item
    code, body = get(f"/zones/{zone_id}/dns_records?name={HOST}")
    item = status(code, body)
    if body.get("success"):
        item["records"] = [{"type": r.get("type"), "proxied": r.get("proxied")} for r in body.get("result", []) if r.get("name") == HOST]
    report["modo_dns"] = item
    code, body = get(f"/zones/{zone_id}/logs/control/retention/flag")
    item = status(code, body)
    if body.get("success"):
        item["enabled"] = body.get("result", {}).get("flag")
    report["logpull_retention"] = item
    code, body = get(f"/zones/{zone_id}/logpush/jobs")
    item = status(code, body)
    if body.get("success"):
        jobs = body.get("result", [])
        relevant = [j for j in jobs if not j.get("filter") or HOST in json.dumps(j.get("filter"))]
        item["potentially_includes_modo"] = [{"enabled": j.get("enabled"), "dataset": j.get("dataset"), "has_filter": bool(j.get("filter"))} for j in relevant]
    report["logpush"] = item

print(json.dumps(report, indent=2))
