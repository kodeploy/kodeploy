package marker

import "regexp"

// sourceRe는 init 스크립트(job.cloneSource)가 clone 직후 찍는 줄이다. 줄 전체가 맞아야 하고,
// 커밋은 정확히 40자 소문자 hex다 (39·41자, 대문자가 섞인 값은 맞지 않는다).
var sourceRe = regexp.MustCompile(`^KODEPLOY_SOURCE_SHA=([0-9a-f]{40})$`)

// Source는 빌드 로그에서 실제로 빌드한 앱 소스 커밋을 찾는다. 고루틴 하나에서만 쓴다.
//
// init 컨테이너 줄만 본다. logs.Follower는 init 줄을 다 보낸 뒤 main 머리줄(mainHeader)을 한 번 보내고
// main(buildkit) 줄을 보낸다. 그래서 그 머리줄과 같은 줄을 만난 뒤로는 아무것도 인정하지 않는다.
// main 로그는 사용자 Dockerfile의 RUN이 아무 글자나 찍을 수 있다 (push 마커에서 가짜 줄을 막은 것과 같은 이유).
// init 쪽은 clone 직후 이 줄을 찍기 전까지 사용자 저장소 내용을 출력하지 않으므로, 처음 맞은 줄만 쓴다.
type Source struct {
	mainHeader string
	inMain     bool
	found      bool
}

// NewSource는 Source를 만든다. mainHeader는 logs.MainHeader다 (import 순환을 피해 값으로 받는다).
func NewSource(mainHeader string) *Source {
	return &Source{mainHeader: mainHeader}
}

// Feed는 init 구간에서 처음 맞은 줄일 때만 (커밋, true)를 돌려준다.
func (s *Source) Feed(line string) (string, bool) {
	if s.found || s.inMain {
		return "", false
	}
	if line == s.mainHeader {
		s.inMain = true
		return "", false
	}
	if m := sourceRe.FindStringSubmatch(line); m != nil {
		s.found = true
		return m[1], true
	}
	return "", false
}
