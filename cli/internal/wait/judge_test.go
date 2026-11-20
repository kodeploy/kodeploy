package wait

import "testing"

func TestJudge(t *testing.T) {
	cases := []struct {
		name     string
		statuses []string
		want     Result
	}{
		{"모두 running", []string{"running", "running"}, Success},
		{"하나만 있고 running", []string{"running"}, Success},
		{"하나 failed", []string{"running", "failed"}, Failed},
		{"진행 중이어도 failed가 있으면 실패", []string{"building", "failed"}, Failed},
		{"cancelled만", []string{"cancelled"}, Superseded},
		{"cancelled와 running", []string{"running", "cancelled"}, Superseded},
		{"failed와 cancelled 섞임은 실패", []string{"cancelled", "failed"}, Failed},
		{"진행 중 남음", []string{"running", "deploying"}, Pending},
		{"모두 진행 중", []string{"queued", "building", "built"}, Pending},
		{"진행 중과 cancelled는 밀림", []string{"building", "cancelled"}, Superseded},
		{"모르는 상태는 진행 중으로", []string{"running", "mystery"}, Pending},
		{"빌드 없음", nil, Pending},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Judge(c.statuses); got != c.want {
				t.Fatalf("Judge(%v) = %v, want %v", c.statuses, got, c.want)
			}
		})
	}
}
