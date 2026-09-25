package api

import (
	"os"
	"path/filepath"
	"testing"
)

// hack/examples의 E2E 요청이 검증을 통과하는지 (예시가 계약과 어긋나지 않게)
func TestExamplesAreValid(t *testing.T) {
	files, err := filepath.Glob("../../hack/examples/*.json")
	if err != nil || len(files) == 0 {
		t.Fatalf("no examples: %v", err)
	}
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		req, err := decodeRequest(b)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if err := testValidator.Validate(req); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
	}
}
