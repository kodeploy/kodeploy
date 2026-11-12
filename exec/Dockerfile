# 빌드 스테이지 - builder와 같다 (client-go v0.35가 Go 1.25를 요구한다)
FROM --platform=$BUILDPLATFORM golang:1.25 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o /out/kodeploy-exec ./cmd/kodeploy-exec

# 실행 스테이지 - 셸 없는 정적 바이너리 전용 이미지, UID 65532
FROM gcr.io/distroless/static:nonroot
COPY --from=build /out/kodeploy-exec /kodeploy-exec
USER 65532:65532
# 8080 공개 문, 8081 내부 문, 9090 지표·healthz
EXPOSE 8080 8081 9090
ENTRYPOINT ["/kodeploy-exec"]
