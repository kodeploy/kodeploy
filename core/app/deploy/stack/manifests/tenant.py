"""테넌트 ns 프로비저닝 매니페스트 (Namespace + core RoleBinding + ghcr-auth Secret + kodeploy-exec HMAC·TLS Secret)."""

import base64
import secrets
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.deploy.stack.manifests._renderer import render, render_all

# kodeploy-exec 내부 문 서명 키. 차트의 exec Deployment가 이 Secret을 /etc/kodeploy-exec/hmac로 마운트한다.
EXEC_HMAC_SECRET = "kodeploy-exec-hmac"
EXEC_HMAC_KEY = "hmac"

# kodeploy-exec 내부 문 TLS 인증서. 차트가 /etc/kodeploy-exec-tls로 마운트하고, core는 이 인증서 하나만 믿는다(자체 서명).
# 만료로 내부 문이 멈추지 않게 길게 잡는다. 바꿀 때는 Secret을 지우고 다시 만든다 (exec_tls 아래 주석).
EXEC_TLS_SECRET = "kodeploy-exec-tls"
EXEC_TLS_DAYS = 3650


# 테넌트 ns 한 묶음 — Namespace, RoleBinding(core 권한), Secret(ghcr-auth) 3개 dict 반환 (이 순서로 만든다).
# 호출 측에서 list 순회하며 K8s API로 apply.
# dockerconfigjson_b64는 호출자가 운영 ghcr-auth Secret을 K8s API로 read해서 전달.
# (ResourceQuota는 제거됨 — Pod limit은 런타임 템플릿이 직접 박고, ns 상한은
#  컴포넌트 구성이 고정이라 구조적으로 결정됨. runtimes.py 주석 참고.)
def tenant(
    tenant_id: str,
    user_id: str,
    dockerconfigjson_b64: str,
) -> list[dict]:
    return render_all(
        "tenant.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        dockerconfigjson_b64=dockerconfigjson_b64,
    )


# core RoleBinding만 — RoleBinding이 빠진 ns를 지우기 전에 다시 건다 (resources.rebind_core).
# 같은 템플릿에서 꺼내서 모양이 ns 프로비저닝 때와 어긋나지 않는다 (admission 정책이 이 모양 하나만 허용).
def core_binding(tenant_id: str) -> dict:
    docs = tenant(tenant_id=tenant_id, user_id="", dockerconfigjson_b64="")
    return next(d for d in docs if d["kind"] == "RoleBinding")


# kodeploy-exec HMAC Secret — 테넌트마다 무작위 32바이트. ns 프로비저닝 때 같이 만든다 (이미 있으면 409로 둔다).
def exec_hmac(tenant_id: str) -> dict:
    return render(
        "exec_hmac.yaml.j2",
        tenant_id=tenant_id,
        secret_name=EXEC_HMAC_SECRET,
        key_name=EXEC_HMAC_KEY,
        key_b64=base64.b64encode(secrets.token_bytes(32)).decode(),
    )


# 게이트웨이 내부 문의 주소 이름. core가 부르는 주소이고, 인증서의 이름도 이것이다.
def exec_host(tenant_id: str) -> str:
    return f"kodeploy-exec.{tenant_id}.svc"


# 테넌트마다 자체 서명 인증서 하나 (ECDSA P-256). 스스로를 서명한 CA라서 core는 이것만 신뢰 목록에 넣고 확인한다.
# 키는 PKCS8 PEM — 게이트웨이(Go tls.LoadX509KeyPair)가 그대로 읽는다.
def _self_signed(tenant_id: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    host = exec_host(tenant_id)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=EXEC_TLS_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host), x509.DNSName(f"{host}.cluster.local")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False, key_agreement=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(ski, critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


# kodeploy-exec TLS Secret — HMAC Secret과 같이 ns 프로비저닝 때 만든다 (이미 있으면 409로 둔다).
# 바꾸는 절차: Secret을 지우고 다음 배포(또는 app.exec.backfill_tls)가 새로 만들게 한 뒤, kubelet이 파일을 바꾸면
# 게이트웨이는 다음 연결부터 새 인증서를 내놓고, core는 인증서 캐시(5분)가 지나거나 확인에 실패하면 새로 읽는다.
def exec_tls(tenant_id: str) -> dict:
    cert_pem, key_pem = _self_signed(tenant_id)
    return render(
        "exec_tls.yaml.j2",
        tenant_id=tenant_id,
        secret_name=EXEC_TLS_SECRET,
        crt_b64=base64.b64encode(cert_pem).decode(),
        key_b64=base64.b64encode(key_pem).decode(),
    )
