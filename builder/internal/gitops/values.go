// Package gitops는 kodeploy-apps의 apps/<namespace>/values.yaml을 읽고, 칸 주인 규칙(지시서 3-4)으로
// 병합해 GitHub Contents API로 커밋한다. 쓰기는 고루틴 하나가 순서대로 처리한다.
package gitops

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"reflect"
	"regexp"

	"go.yaml.in/yaml/v3"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

// Values는 차트 values.yaml의 전체 칸이다. 필드 순서 = 직렬화 순서 = 차트 values.yaml·기존 파일의 순서.
type Values struct {
	Name        string   `yaml:"name"`
	UserID      string   `yaml:"userId"`
	Runtime     string   `yaml:"runtime"`
	Port        *int     `yaml:"port"`
	Replicas    int      `yaml:"replicas"`
	Image       string   `yaml:"image"`
	EnvRevision int      `yaml:"envRevision"`
	DB          string   `yaml:"db"`
	Redis       bool     `yaml:"redis"`
	Volume      Volume   `yaml:"volume"`
	Static      Static   `yaml:"static"`
	Hostnames   []string `yaml:"hostnames"`
}

type Volume struct {
	MountPath string `yaml:"mountPath"`
}

type Static struct {
	Enabled   bool     `yaml:"enabled"`
	Image     string   `yaml:"image"`
	Hostnames []string `yaml:"hostnames"`
}

// Empty는 파일이 없을 때의 시작값이다. kodeploy-charts/charts/app/values.yaml의 기본값과 같다
// (지시서의 image: "", runtime: none, port: null 포함).
func Empty() *Values {
	return &Values{Runtime: "none", Replicas: 1, DB: "none", Hostnames: []string{}, Static: Static{Hostnames: []string{}}}
}

var namespaceRe = regexp.MustCompile(`^(tenant|app)-[a-f0-9]{8}$`)

// Path는 커밋할 수 있는 유일한 경로다. namespace 형식이 틀리면 오류.
func Path(namespace string) (string, error) {
	if !namespaceRe.MatchString(namespace) {
		return "", fmt.Errorf("refusing path for namespace %q", namespace)
	}
	return "apps/" + namespace + "/values.yaml", nil
}

// Parse는 기존 파일을 읽는다. 없는 칸은 차트 기본값, 모르는 칸은 오류.
func Parse(b []byte) (*Values, error) {
	v := Empty()
	dec := yaml.NewDecoder(bytes.NewReader(b))
	dec.KnownFields(true)
	if err := dec.Decode(v); err != nil && !errors.Is(err, io.EOF) { // 빈 파일 = 기본값
		return nil, fmt.Errorf("values.yaml: %w", err)
	}
	v.normalize()
	return v, nil
}

// Marshal은 키 순서를 고정해 직렬화한다 (들여쓰기 2칸, 기존 파일과 같은 모양).
func (v *Values) Marshal() ([]byte, error) {
	c := v.clone()
	c.normalize()
	var buf bytes.Buffer
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(2)
	if err := enc.Encode(c); err != nil {
		return nil, err
	}
	if err := enc.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// Equal은 의미가 같은지다 (nil과 빈 목록은 같다).
func (v *Values) Equal(o *Values) bool {
	a, b := v.clone(), o.clone()
	a.normalize()
	b.normalize()
	return reflect.DeepEqual(a, b)
}

func (v *Values) normalize() {
	if v.Hostnames == nil {
		v.Hostnames = []string{}
	}
	if v.Static.Hostnames == nil {
		v.Static.Hostnames = []string{}
	}
}

func (v *Values) clone() *Values {
	c := *v
	if v.Port != nil {
		p := *v.Port
		c.Port = &p
	}
	c.Hostnames = append([]string(nil), v.Hostnames...)
	c.Static.Hostnames = append([]string(nil), v.Static.Hostnames...)
	return &c
}

var ErrNoValuesFile = errors.New("values file does not exist")

// Merge는 지시서 3-4 규칙으로 커밋할 값을 만든다. cur는 git의 현재 값(없으면 nil),
// image는 build·set-image에서 요청 slot 칸에 넣을 <repo>:<tag>@sha256:<hex>.
//   - core 소유 칸: req.Values에 온 것만 덮는다.
//   - 요청 slot의 묶인 칸: build·set-image면 image(+서버는 unit의 runtime·port). config면 그대로.
//   - 다른 slot의 묶인 칸: 그대로.
func Merge(cur *Values, req *contract.DeployRequest, image string) (*Values, error) {
	var v *Values
	if cur == nil {
		if req.Kind == contract.KindConfig {
			return nil, ErrNoValuesFile // 앱이 없는데 설정만 바꿀 수는 없다
		}
		v = Empty()
	} else {
		v = cur.clone()
	}
	applyCore(v, req.Values)

	switch req.Kind {
	case contract.KindBuild, contract.KindSetImage:
		if req.Slot == contract.SlotStatic {
			v.Static.Image = image
			break
		}
		if req.Unit == nil {
			return nil, errors.New("unit is required for the server slot")
		}
		v.Image = image
		v.Runtime = req.Unit.Runtime
		port := req.Unit.Port
		v.Port = &port
	case contract.KindConfig:
	default:
		return nil, fmt.Errorf("kind %q does not merge values", req.Kind)
	}
	return v, nil
}

func applyCore(v *Values, cv *contract.CoreValues) {
	if cv == nil {
		return
	}
	set := func(dst *string, src *string) {
		if src != nil {
			*dst = *src
		}
	}
	set(&v.Name, cv.Name)
	set(&v.UserID, cv.UserID)
	set(&v.DB, cv.DB)
	if cv.Replicas != nil {
		v.Replicas = *cv.Replicas
	}
	if cv.EnvRevision != nil {
		v.EnvRevision = *cv.EnvRevision
	}
	if cv.Redis != nil {
		v.Redis = *cv.Redis
	}
	if cv.Volume != nil {
		set(&v.Volume.MountPath, cv.Volume.MountPath)
	}
	if cv.Static != nil {
		if cv.Static.Enabled != nil {
			v.Static.Enabled = *cv.Static.Enabled
		}
		if cv.Static.Hostnames != nil {
			v.Static.Hostnames = append([]string{}, *cv.Static.Hostnames...)
		}
	}
	if cv.Hostnames != nil {
		v.Hostnames = append([]string{}, *cv.Hostnames...)
	}
}
