// kodeploy — KoDeploy 재배포 CLI. GitHub Actions와 터미널에서 재배포를 요청하고 끝날 때까지 기다린다.
// 계약: docs/deploy-api-contract.md (재배포 3절, 상태 4절, 판정·exit code 5절, CI 인증 9절)
package main

import (
	"os"

	"github.com/kodeploy/kodeploy/cli/internal/cmd"
)

func main() {
	os.Exit(cmd.Main())
}
