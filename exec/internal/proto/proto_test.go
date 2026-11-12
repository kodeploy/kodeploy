package proto

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestFrameRoundTrip(t *testing.T) {
	payload := []byte("한글 \x00\xff 바이너리")
	ch, p, err := Parse(Frame(ChStdout, payload))
	if err != nil || ch != ChStdout || !bytes.Equal(p, payload) {
		t.Fatalf("got %d %q %v", ch, p, err)
	}
	if _, _, err := Parse(nil); err != ErrEmptyFrame {
		t.Fatalf("empty frame: %v", err)
	}
	// 채널만 있고 내용이 없는 프레임은 유효하다 (빈 stdin)
	if ch, p, err := Parse([]byte{ChStdin}); err != nil || ch != ChStdin || len(p) != 0 {
		t.Fatalf("channel only: %d %q %v", ch, p, err)
	}
}

func TestCloseFrame(t *testing.T) {
	if got := CloseFrame(ChStdin); !bytes.Equal(got, []byte{255, 0}) {
		t.Fatalf("got %v", got)
	}
}

func TestSize(t *testing.T) {
	s, err := ParseSize([]byte(`{"Width":120,"Height":32}`))
	if err != nil || s.Width != 120 || s.Height != 32 {
		t.Fatalf("got %+v %v", s, err)
	}
	for _, bad := range []string{`{"Width":0,"Height":32}`, `{"Width":120}`, `{"Width":5000,"Height":10}`, `nope`, `{"Width":-1,"Height":2}`} {
		if _, err := ParseSize([]byte(bad)); err == nil {
			t.Errorf("%s: want error", bad)
		}
	}
	if got := string(EncodeSize(80, 24)); got != `{"Width":80,"Height":24}` {
		t.Fatalf("encode: %s", got)
	}
}

// core dbquery._parse_exit가 읽는 모양과 같아야 한다: status, details.causes[].reason == "ExitCode", message == 코드
func TestStatusShape(t *testing.T) {
	var ok map[string]any
	_ = json.Unmarshal(MarshalStatus(ExitStatus(0)), &ok)
	if ok["status"] != "Success" {
		t.Fatalf("success: %v", ok)
	}

	var fail struct {
		Status  string `json:"status"`
		Details struct {
			Causes []struct {
				Reason  string `json:"reason"`
				Message string `json:"message"`
			} `json:"causes"`
		} `json:"details"`
	}
	if err := json.Unmarshal(MarshalStatus(ExitStatus(2)), &fail); err != nil {
		t.Fatal(err)
	}
	if fail.Status != "Failure" || len(fail.Details.Causes) != 1 ||
		fail.Details.Causes[0].Reason != "ExitCode" || fail.Details.Causes[0].Message != "2" {
		t.Fatalf("failure shape: %+v", fail)
	}

	for _, code := range []int{0, 1, 2, 127} {
		if got := StatusExitCode(MarshalStatus(ExitStatus(code))); got != code {
			t.Errorf("exit %d: got %d", code, got)
		}
	}
	if got := StatusExitCode(MarshalStatus(ErrorStatus("boom"))); got != 1 {
		t.Errorf("error status: got %d", got)
	}
}
