// Package marker는 BuildKit 로그에서 "이 빌드의 이미지 push가 끝났다"는 줄을 찾아 digest를 뽑는다.
//
// 원본(core/app/deploy/build/pipeline.py):
//
//	_EXPORT_IMAGE_RE = re.compile(r"exporting to image")
//	_PUSH_DONE_RE    = re.compile(r"pushing manifest .*\bdone\b")   # 앵커 뒤 첫 매치
//
// 여기서는 같은 순서(앵커 → push 완료)를 지키되 두 가지로 좁힌다.
//   - 줄 머리가 BuildKit 진행 줄("#<n> ")이어야 한다. 유저 RUN 출력은 "#<n> <경과초> ..." 꼴이라 걸러진다.
//   - push 줄의 이미지가 이 빌드의 <image_repo>:<image_tag>여야 하고, 64자 digest를 뽑는다.
//     그래서 캐시(:buildcache)나 다른 이미지의 push 줄은 맞지 않는다.
//
// 원본 정규식이 맞추지 못하는 줄은 여기서도 맞지 않는다 (좁히기만 했다. marker_test가 확인).
// kodeploy-bench/markers.py는 원본 정규식과 동기화 대상이며, 이 파일은 그 대상이 아니다.
package marker

import "regexp"

var exportRe = regexp.MustCompile(`^#\d+ exporting to image\b`)

// Matcher는 한 빌드의 로그를 줄 단위로 받는다. 고루틴 하나에서만 쓴다.
type Matcher struct {
	push      *regexp.Regexp
	exporting bool
	found     bool
}

func New(imageRepo, imageTag string) *Matcher {
	return &Matcher{push: regexp.MustCompile(
		`^#\d+ pushing manifest for ` + regexp.QuoteMeta(imageRepo+":"+imageTag) +
			`@sha256:([a-f0-9]{64})\b.*\bdone\b`,
	)}
}

// Feed는 push 완료 줄을 처음 만났을 때만 ("sha256:<hex>", true)를 돌려준다.
func (m *Matcher) Feed(line string) (string, bool) {
	if m.found {
		return "", false
	}
	if !m.exporting {
		m.exporting = exportRe.MatchString(line)
		return "", false
	}
	if sm := m.push.FindStringSubmatch(line); sm != nil {
		m.found = true
		return "sha256:" + sm[1], true
	}
	return "", false
}
