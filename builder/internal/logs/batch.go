package logs

import "time"

// Batch는 in의 줄을 모아 interval마다, 또는 max줄이 차면 emit한다 (지시서 3-2: 1초 배치, 이벤트당 200줄).
// in이 닫히면 남은 줄을 내보내고 끝난다.
func Batch(in <-chan string, interval time.Duration, max int, emit func([]string)) {
	t := time.NewTicker(interval)
	defer t.Stop()
	var buf []string
	flush := func() {
		if len(buf) > 0 {
			emit(buf)
			buf = nil
		}
	}
	for {
		select {
		case l, ok := <-in:
			if !ok {
				flush()
				return
			}
			buf = append(buf, l)
			if len(buf) >= max {
				flush()
			}
		case <-t.C:
			flush()
		}
	}
}
