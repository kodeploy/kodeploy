package gitops

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

func demo(t *testing.T) (*Values, []byte) {
	t.Helper()
	b, err := os.ReadFile("testdata/demo-values.yaml")
	if err != nil {
		t.Fatal(err)
	}
	v, err := Parse(b)
	if err != nil {
		t.Fatal(err)
	}
	return v, b
}

// 빌더가 다시 쓴 파일이 지금 kodeploy-apps의 파일과 글자 단위로 같아야 첫 커밋에서 diff가 생기지 않는다.
func TestRoundTripKeepsExistingFileByteForByte(t *testing.T) {
	v, raw := demo(t)
	out, err := v.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != string(raw) {
		t.Fatalf("round trip changed the file\n--- got\n%s--- want\n%s", out, raw)
	}
}

func TestParseDefaultsAndUnknownKeys(t *testing.T) {
	v, err := Parse(nil)
	if err != nil || !v.Equal(Empty()) {
		t.Fatalf("empty file: %+v %v", v, err)
	}
	v, err = Parse([]byte("name: a\n"))
	if err != nil || v.Name != "a" || v.Runtime != "none" || v.DB != "none" || v.Replicas != 1 || v.Port != nil {
		t.Fatalf("partial file should get chart defaults: %+v %v", v, err)
	}
	if _, err := Parse([]byte("name: a\nsize: L\n")); err == nil {
		t.Fatal("unknown key accepted")
	}
	if _, err := Parse([]byte("static:\n  enabled: true\n  cdn: true\n")); err == nil {
		t.Fatal("unknown nested key accepted")
	}
}

func TestEmptyMatchesChartDefaults(t *testing.T) {
	out, err := Empty().Marshal()
	if err != nil {
		t.Fatal(err)
	}
	want := `name: ""
userId: ""
runtime: none
port: null
replicas: 1
image: ""
envRevision: 0
db: none
redis: false
volume:
  mountPath: ""
static:
  enabled: false
  image: ""
  hostnames: []
hostnames: []
`
	if string(out) != want {
		t.Fatalf("got\n%s", out)
	}
}

const newDigest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"

func str(s string) *string { return &s }

// kind 3종 × slot 2종. 요청 slot의 묶인 칸만 바뀌고, 다른 slot 칸은 그대로, core 칸은 보낸 것만 바뀐다.
func TestMergeMatrix(t *testing.T) {
	serverImg := "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:3f9a2c1d@" + newDigest
	staticImg := "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring-site:3f9a2c1d@" + newDigest

	for _, kind := range []string{contract.KindBuild, contract.KindSetImage, contract.KindConfig} {
		for _, slot := range []string{contract.SlotServer, contract.SlotStatic} {
			t.Run(kind+"/"+slot, func(t *testing.T) {
				cur, _ := demo(t)
				before := cur.clone()
				req := &contract.DeployRequest{Kind: kind, Slot: slot,
					Values: &contract.CoreValues{EnvRevision: intp(7), Static: &contract.CoreStatic{Enabled: boolp(true)}}}
				img := ""
				if kind != contract.KindConfig {
					if slot == contract.SlotServer {
						img = serverImg
						req.Unit = &contract.Unit{Runtime: "python", Port: 8000}
					} else {
						img = staticImg
					}
				}
				got, err := Merge(cur, req, img)
				if err != nil {
					t.Fatal(err)
				}
				if !cur.Equal(before) {
					t.Fatal("Merge must not modify its input")
				}

				// core 칸: 보낸 것만
				if got.EnvRevision != 7 || !got.Static.Enabled {
					t.Fatalf("core values not applied: %+v", got)
				}
				if got.Name != before.Name || got.DB != before.DB || len(got.Hostnames) != 2 {
					t.Fatalf("unsent core values changed: %+v", got)
				}

				switch {
				case kind == contract.KindConfig:
					if got.Image != before.Image || got.Static.Image != before.Static.Image ||
						got.Runtime != before.Runtime || *got.Port != *before.Port {
						t.Fatalf("config changed bound fields: %+v", got)
					}
				case slot == contract.SlotServer:
					if got.Image != serverImg || got.Runtime != "python" || *got.Port != 8000 {
						t.Fatalf("server slot not updated: %+v", got)
					}
					if got.Static.Image != before.Static.Image {
						t.Fatal("static image touched by server request")
					}
				default:
					if got.Static.Image != staticImg {
						t.Fatalf("static slot not updated: %+v", got)
					}
					if got.Image != before.Image || got.Runtime != before.Runtime || *got.Port != *before.Port {
						t.Fatal("server fields touched by static request")
					}
				}
			})
		}
	}
}

func TestMergeWithoutFile(t *testing.T) {
	req := &contract.DeployRequest{Kind: contract.KindSetImage, Slot: contract.SlotServer,
		Unit:   &contract.Unit{Runtime: "java", Port: 8080},
		Values: &contract.CoreValues{Name: str("shop"), UserID: str("d6d8b75985524d6f9a9000665e7ca0da"), Hostnames: &[]string{"shop.kodeploy.com"}}}
	got, err := Merge(nil, req, "ghcr.io/u/d6d8b759/shop:v1@"+newDigest)
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "shop" || got.Runtime != "java" || got.DB != "none" || got.Replicas != 1 || got.Hostnames[0] != "shop.kodeploy.com" {
		t.Fatalf("got %+v", got)
	}

	if _, err := Merge(nil, &contract.DeployRequest{Kind: contract.KindConfig, Slot: "server", Values: &contract.CoreValues{}}, ""); !errors.Is(err, ErrNoValuesFile) {
		t.Fatalf("config without file: %v", err)
	}
}

// 금지 키는 요청을 읽는 단계(contract)에서 거부된다. 병합까지 오지 않는다.
func TestForbiddenKeysNeverReachMerge(t *testing.T) {
	for _, values := range []string{`{"image":"x"}`, `{"runtime":"java"}`, `{"port":1}`, `{"static":{"image":"x"}}`} {
		var req contract.DeployRequest
		err := json.Unmarshal([]byte(`{"kind":"config","values":`+values+`}`), &req)
		var fe *contract.ForbiddenKeyError
		if !errors.As(err, &fe) {
			t.Fatalf("%s: got %v", values, err)
		}
	}
}

func TestPath(t *testing.T) {
	if p, err := Path("tenant-d6d8b759"); err != nil || p != "apps/tenant-d6d8b759/values.yaml" {
		t.Fatalf("got %q %v", p, err)
	}
	for _, ns := range []string{"", "../x", "tenant-d6d8b759/../../charts", "kube-system", "tenant-D6D8B759"} {
		if _, err := Path(ns); err == nil || !strings.Contains(err.Error(), "refusing") {
			t.Fatalf("%q accepted", ns)
		}
	}
}

func intp(i int) *int    { return &i }
func boolp(b bool) *bool { return &b }
