// Go 서버(net/http · Gin · Echo 등) 가이드 - 단순 톤.
// KoDeploy는 앱을 비-root(UID 1000)로 실행하고, PORT 환경변수(기본 8080)를 자동 주입한다.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Go() {
  return (
    <>
      <Section title="빌드 방식">
        <p className="text-fg-2 mb-3">
          <Code>go.mod</Code>가 있으면 별도 Dockerfile 없이 <Code>자동 빌드</Code>가 가능해요.
          배포 폼 빌드 방식을 <Code>자동 감지</Code>으로 두면 모듈을 받아 빌드하고 만들어진
          바이너리를 실행합니다. 직접 제어하고 싶으면 Dockerfile을 쓰면 됩니다.
        </p>
        <Bullet>
          <Code>main</Code> 패키지가 저장소 루트에 없으면(<Code>cmd/server</Code> 등) Dockerfile로
          빌드 대상을 지정하세요.
        </Bullet>
      </Section>

      <Section title="포트 바인딩 (중요)">
        <p className="text-fg-2 mb-3">
          KoDeploy가 <Code>PORT</Code> 환경변수(기본 <Code>8080</Code>)를 주입해요. 앱은 배포
          설정과 같은 포트에서 연결을 받아야 해요. <Code>PORT</Code>를 읽으면 설정 변경에도
          맞춰 실행됩니다.
        </p>
        <CodeBlock>
{`package main

import (
	"fmt"
	"net/http"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "hello from KoDeploy")
	})
	// 0.0.0.0에서 연결을 받아야 외부에서 닿아요.
	http.ListenAndServe("0.0.0.0:"+port, nil)
}`}
        </CodeBlock>
      </Section>

      <Section title="Dockerfile로 배포할 때">
        <p className="text-fg-2 mb-3">
          멀티 스테이지로 만들면 이미지가 작아져요. 정적 바이너리(<Code>CGO_ENABLED=0</Code>)를
          비-root 사용자로 실행하세요.
        </p>
        <CodeBlock>
{`FROM golang:1.23 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app ./...

FROM gcr.io/distroless/static:nonroot
COPY --from=build /app /app
ENTRYPOINT ["/app"]`}
        </CodeBlock>
      </Section>

      <Section title="MySQL 쓸 때">
        <p className="text-fg-2 mb-3">
          MySQL을 켜면 접속 정보가 <Code>DB_*</Code> 환경변수로 자동 주입돼요.{" "}
          <Code>os.Getenv</Code>로 바로 읽으면 됩니다 (PostgreSQL도 같은 변수, 호스트만{" "}
          <Code>postgres</Code>·포트 <Code>5432</Code>).
        </p>
        <CodeBlock>
{`dsn := fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?parseTime=true",
	os.Getenv("DB_USER"),     // app
	os.Getenv("DB_PASSWORD"),
	os.Getenv("DB_HOST"),     // mysql
	os.Getenv("DB_PORT"),     // 3306
	os.Getenv("DB_NAME"),     // app
)
db, err := sql.Open("mysql", dsn)`}
        </CodeBlock>
      </Section>

      <Section title="메모리">
        <Bullet>
          메모리 한도는 <Code>256Mi</Code>예요. 고루틴을 무한정 만들거나 큰 데이터를 메모리에
          쌓으면 OOM으로 재시작될 수 있어요.
        </Bullet>
      </Section>

      <Section title="업로드 파일은 영속저장소에">
        <Bullet>
          컨테이너 안에 저장된 파일(업로드 이미지·첨부파일)은{" "}
          <Code>재배포·재시작 때 사라져요</Code>. 업로드가 있는 앱은 영속저장소가 필요합니다.
        </Bullet>
        <Bullet>
          배포 폼 <Code>실행 환경 → 스토리지 → 영구 저장소</Code>를 켜고 업로드 디렉토리를
          마운트 경로로 지정하세요. 저장소를 꺼도 데이터는 보존됩니다.
        </Bullet>
      </Section>
    </>
  );
}
