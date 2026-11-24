package innertls

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const host = "kodeploy-exec.tenant-0a1b2c3d.svc"

// selfSigned는 core가 만드는 것과 같은 모양(자체 서명, CA, 서비스 DNS 이름)의 인증서를 만든다.
func selfSigned(t *testing.T) (certPEM, keyPEM []byte) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()), Subject: pkix.Name{CommonName: host},
		DNSNames: []string{host}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true,
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	kder, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kder})
}

func writePair(t *testing.T, dir string, certPEM, keyPEM []byte) *Certs {
	t.Helper()
	c := &Certs{CertFile: filepath.Join(dir, "tls.crt"), KeyFile: filepath.Join(dir, "tls.key")}
	if err := os.WriteFile(c.CertFile, certPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(c.KeyFile, keyPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	return c
}

// serve는 감싼 리스너로 "ok"만 돌려주는 HTTP 서버를 띄우고 주소를 돌려준다.
func serve(t *testing.T, certs *Certs, required bool) string {
	t.Helper()
	inner, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	l := Listen(inner, Config(certs), required)
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.TLS != nil {
			_, _ = io.WriteString(w, "ok-tls")
			return
		}
		_, _ = io.WriteString(w, "ok-plain")
	}), ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = srv.Serve(l) }()
	t.Cleanup(func() { _ = srv.Close() })
	return inner.Addr().String()
}

func pinned(t *testing.T, certPEM []byte) *http.Client {
	t.Helper()
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(certPEM) {
		t.Fatal("bad pem")
	}
	return &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{
		TLSClientConfig: &tls.Config{RootCAs: pool, ServerName: host, MinVersion: tls.VersionTLS13}}}
}

func get(c *http.Client, url string) (string, error) {
	resp, err := c.Get(url)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	return string(b), err
}

var plain = &http.Client{Timeout: 5 * time.Second}

func TestPlainAndTLSOnSamePort(t *testing.T) {
	cp, kp := selfSigned(t)
	addr := serve(t, writePair(t, t.TempDir(), cp, kp), false)
	if got, err := get(plain, "http://"+addr); err != nil || got != "ok-plain" {
		t.Fatalf("plain: %q %v", got, err)
	}
	if got, err := get(pinned(t, cp), "https://"+addr); err != nil || got != "ok-tls" {
		t.Fatalf("tls: %q %v", got, err)
	}
}

func TestRequiredDropsPlain(t *testing.T) {
	cp, kp := selfSigned(t)
	addr := serve(t, writePair(t, t.TempDir(), cp, kp), true)
	if got, err := get(plain, "http://"+addr); err == nil {
		t.Fatalf("plain must be refused when required, got %q", got)
	}
	if got, err := get(pinned(t, cp), "https://"+addr); err != nil || got != "ok-tls" {
		t.Fatalf("tls: %q %v", got, err)
	}
}

func TestMissingCertOnlyFailsTLS(t *testing.T) {
	dir := t.TempDir()
	addr := serve(t, &Certs{CertFile: filepath.Join(dir, "tls.crt"), KeyFile: filepath.Join(dir, "tls.key")}, false)
	cp, _ := selfSigned(t)
	if _, err := get(pinned(t, cp), "https://"+addr); err == nil {
		t.Fatal("tls must fail without cert files")
	}
	if got, err := get(plain, "http://"+addr); err != nil || got != "ok-plain" {
		t.Fatalf("plain must still work: %q %v", got, err)
	}
}

func TestWrongPinRejected(t *testing.T) {
	cp, kp := selfSigned(t)
	other, _ := selfSigned(t)
	addr := serve(t, writePair(t, t.TempDir(), cp, kp), true)
	if _, err := get(pinned(t, other), "https://"+addr); err == nil {
		t.Fatal("client pinned to another cert must not connect")
	}
}

func TestCertReloadsWhenFilesChange(t *testing.T) {
	dir := t.TempDir()
	cp1, kp1 := selfSigned(t)
	certs := writePair(t, dir, cp1, kp1)
	addr := serve(t, certs, true)
	if _, err := get(pinned(t, cp1), "https://"+addr); err != nil {
		t.Fatal(err)
	}
	cp2, kp2 := selfSigned(t)
	writePair(t, dir, cp2, kp2)
	later := time.Now().Add(2 * time.Second) // 파일 시스템 시각 해상도와 상관없이 바뀐 것으로 보이게
	_ = os.Chtimes(certs.CertFile, later, later)
	_ = os.Chtimes(certs.KeyFile, later, later)
	c2 := pinned(t, cp2)
	c2.Transport.(*http.Transport).DisableKeepAlives = true
	if got, err := get(c2, "https://"+addr); err != nil || got != "ok-tls" {
		t.Fatalf("new cert not served: %q %v", got, err)
	}
	c1 := pinned(t, cp1)
	if _, err := get(c1, "https://"+addr); err == nil {
		t.Fatal("old pin must stop working after rotation")
	}
}

func TestSilentConnDoesNotBlockOthers(t *testing.T) {
	cp, kp := selfSigned(t)
	addr := serve(t, writePair(t, t.TempDir(), cp, kp), false)
	idle, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer idle.Close()
	start := time.Now()
	if got, err := get(plain, "http://"+addr); err != nil || got != "ok-plain" {
		t.Fatalf("%q %v", got, err)
	}
	if time.Since(start) > 2*time.Second {
		t.Fatalf("a silent connection delayed others: %v", time.Since(start))
	}
}
