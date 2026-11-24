// Package innertls는 내부 문(8081)의 TLS를 맡는다.
//
// 인증서는 core가 테넌트마다 자체 서명으로 만들어 kodeploy-exec-tls Secret에 넣고, core는 그 인증서 하나만 믿는다.
// 옮기는 동안 core가 아직 평문으로 부를 수 있어서, 같은 포트에서 첫 바이트로 TLS와 평문을 가른다
// (TLS 핸드셰이크의 첫 바이트는 레코드 타입 0x16, HTTP 요청은 메서드 글자). Required가 켜지면 평문은 끊는다.
package innertls

import (
	"bufio"
	"crypto/tls"
	"errors"
	"net"
	"os"
	"sync"
	"time"
)

// 첫 바이트를 기다리는 시간. 이 안에 아무것도 보내지 않는 연결은 닫는다.
const sniffTimeout = 10 * time.Second

const recordTypeHandshake = 0x16

// Certs는 인증서 파일을 읽어 두고, 파일이 바뀌면(Secret을 갈아 끼우면 kubelet이 파일을 바꾼다) 다시 읽는다.
type Certs struct {
	CertFile, KeyFile string

	mu      sync.Mutex
	cert    *tls.Certificate
	certMod time.Time
	keyMod  time.Time
}

// GetCertificate는 tls.Config.GetCertificate에 넣는다. 파일이 없으면 오류라 TLS 핸드셰이크만 실패한다.
func (c *Certs) GetCertificate(*tls.ClientHelloInfo) (*tls.Certificate, error) {
	ci, err := os.Stat(c.CertFile)
	if err != nil {
		return nil, err
	}
	ki, err := os.Stat(c.KeyFile)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cert != nil && ci.ModTime().Equal(c.certMod) && ki.ModTime().Equal(c.keyMod) {
		return c.cert, nil
	}
	pair, err := tls.LoadX509KeyPair(c.CertFile, c.KeyFile)
	if err != nil {
		return nil, err
	}
	c.cert, c.certMod, c.keyMod = &pair, ci.ModTime(), ki.ModTime()
	return c.cert, nil
}

// Config는 내부 문용 서버 TLS 설정이다. core(Python ssl)는 TLS 1.3을 쓴다.
func Config(c *Certs) *tls.Config {
	return &tls.Config{MinVersion: tls.VersionTLS13, GetCertificate: c.GetCertificate}
}

// Listener는 받은 연결의 첫 바이트를 보고 TLS면 tls.Server로 감싸서, 아니면 그대로 넘긴다.
// 첫 바이트를 기다리는 일은 연결마다 고루틴에서 해서, 느린 연결 하나가 Accept를 막지 않는다.
type Listener struct {
	net.Listener
	tls      *tls.Config
	required bool

	conns chan net.Conn
	errc  chan error
	done  chan struct{}
	once  sync.Once
}

// Listen은 inner를 감싼다. required면 평문 연결은 닫는다.
func Listen(inner net.Listener, cfg *tls.Config, required bool) *Listener {
	l := &Listener{Listener: inner, tls: cfg, required: required,
		conns: make(chan net.Conn), errc: make(chan error, 1), done: make(chan struct{})}
	go l.loop()
	return l
}

func (l *Listener) loop() {
	for {
		c, err := l.Listener.Accept()
		if err != nil {
			select {
			case l.errc <- err:
			case <-l.done:
			}
			return
		}
		go l.classify(c)
	}
}

func (l *Listener) classify(c net.Conn) {
	_ = c.SetReadDeadline(time.Now().Add(sniffTimeout))
	br := bufio.NewReader(c)
	first, err := br.Peek(1)
	_ = c.SetReadDeadline(time.Time{})
	if err != nil {
		_ = c.Close()
		return
	}
	var out net.Conn = &peekedConn{Conn: c, r: br}
	switch {
	case first[0] == recordTypeHandshake:
		out = tls.Server(out, l.tls)
	case l.required:
		_ = c.Close()
		return
	}
	select {
	case l.conns <- out:
	case <-l.done:
		_ = c.Close()
	}
}

// Accept는 가른 연결을 하나씩 돌려준다.
func (l *Listener) Accept() (net.Conn, error) {
	select {
	case c := <-l.conns:
		return c, nil
	case err := <-l.errc:
		return nil, err
	case <-l.done:
		return nil, net.ErrClosed
	}
}

// Close는 바깥 리스너를 닫고 Accept를 깨운다.
func (l *Listener) Close() error {
	l.once.Do(func() { close(l.done) })
	err := l.Listener.Close()
	if errors.Is(err, net.ErrClosed) {
		return nil
	}
	return err
}

// peekedConn은 첫 바이트를 들여다본 bufio.Reader를 거쳐 읽는 연결이다.
type peekedConn struct {
	net.Conn
	r *bufio.Reader
}

func (p *peekedConn) Read(b []byte) (int, error) { return p.r.Read(b) }
