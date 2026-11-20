package cmd

import (
	"crypto/rand"
	"fmt"
)

// randomKey는 cli-<UUID v4>다. 외부 의존 없이 crypto/rand로 만든다.
func randomKey() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("cli-%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
