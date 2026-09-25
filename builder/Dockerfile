# 빌드 스테이지 - client-go v0.35가 Go 1.25를 요구한다 (지시서의 1.23에서 올림)
FROM --platform=$BUILDPLATFORM golang:1.25 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o /out/builder ./cmd/builder

# 실행 스테이지 - 셸 없는 정적 바이너리 전용 이미지, UID 65532
FROM gcr.io/distroless/static:nonroot
COPY --from=build /out/builder /builder
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/builder"]
