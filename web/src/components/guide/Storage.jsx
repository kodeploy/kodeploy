// 저장소 가이드 - 영속 저장소 셀렉터(없음/로컬 PVC/오브젝트 R2)의 선택 기준과 사용법.
// 주입 변수 이름(S3_*/AWS_*)·보존 정책은 백엔드 r2.provision/_apply_volume과 일치시킬 것.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Storage() {
  return (
    <>
      <Section title="어떤 걸 골라야 하나요">
        <p className="text-fg-2 mb-3">
          앱 컨테이너의 디스크는 <Code>재배포·재시작 때 초기화</Code>돼요. 데이터를 남기려면
          배포 폼 <Code>실행 환경 → 스토리지</Code>에서 둘 중 하나를 켜세요.
        </p>
        <Bullet>
          <Code>영구 저장소 (PVC)</Code> - 코드가 <Code>파일 경로에 직접 쓰는</Code> 앱.
          게시판 업로드 폴더, SQLite 파일 등. 지정한 경로가 영속 디스크가 됩니다.
        </Bullet>
        <Bullet>
          <Code>객체 스토리지 (R2)</Code> - 이미지·첨부파일을 <Code>URL로 서빙</Code>하는 앱.
          앱 전용 버킷이 만들어지고 S3 호환 자격증명이 환경변수로 들어와요.
        </Bullet>
        <Bullet>한 앱에 둘 중 하나만 켤 수 있어요 (DB 한 개 정책과 동일).</Bullet>
      </Section>

      <Section title="영구 저장소 (PVC)">
        <Bullet>
          마운트 경로(절대경로)만 입력하면 끝이에요 - 예: <Code>/var/www/html/data</Code>.
          앱은 평소처럼 그 경로에 읽고 쓰면 됩니다.
        </Bullet>
        <Bullet>
          <Code>지정한 경로만</Code> 영속이에요. 그 밖에 쓴 파일은 여전히 재배포 때 사라집니다.
        </Bullet>
        <Bullet>기본 용량은 5Gi입니다.</Bullet>
      </Section>

      <Section title="객체 스토리지 (R2) - 자격증명이 자동으로 들어와요">
        <p className="text-fg-2 mb-3">
          켜면 앱 전용 버킷이 생기고, <Code>그 버킷에만 접근 가능한</Code> 자격증명이
          환경변수로 주입돼요:
        </p>
        <CodeBlock>
          {[
            "S3_ENDPOINT=https://....r2.cloudflarestorage.com",
            "S3_BUCKET=kd-{앱이름}",
            "S3_ACCESS_KEY / S3_SECRET_KEY",
            "S3_PUBLIC_BASE_URL=https://pub-....r2.dev   # 공개 URL 베이스",
            "",
            "# AWS SDK · boto3 표준 이름으로도 함께 들어와요",
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY",
            "AWS_REGION=auto / AWS_ENDPOINT_URL",
          ].join("\n")}
        </CodeBlock>
        <p className="text-fg-2 mt-4 mb-3">
          <Code>AWS_*</Code> 변수 덕분에 boto3·AWS SDK는 <Code>설정 코드 없이</Code> 바로
          동작해요. Python 예시:
        </p>
        <CodeBlock>
{`import os
import boto3

s3 = boto3.client("s3")          # 자격증명·엔드포인트 자동 인식
s3.upload_fileobj(f, os.environ["S3_BUCKET"], "uploads/cat.png")

# 업로드한 객체의 공개 URL
url = f"{os.environ['S3_PUBLIC_BASE_URL']}/uploads/cat.png"`}
        </CodeBlock>
        <Bullet>
          자격증명은 재배포 때마다 새로 발급돼요 - 코드나 설정 파일에 값을 복사해두지 말고{" "}
          <Code>항상 환경변수에서</Code> 읽으세요.
        </Bullet>
        <Bullet>
          버킷 안 파일은 앱 상세 작업 공간의 <Code>스토리지</Code>에서 확인할 수 있어요 -
          목록·이미지 미리보기·URL 복사·삭제.
        </Bullet>
      </Section>

      <Section title="데이터는 보존돼요">
        <Bullet>
          저장소를 <Code>사용 안 함</Code>으로 돌려도 디스크(PVC)·버킷의 데이터는 남아요.
          다시 켜면 그대로 복원됩니다 (DB 토글과 동일 정책).
        </Bullet>
        <Bullet>앱을 완전 삭제할 때만 데이터도 함께 정리됩니다.</Bullet>
      </Section>
    </>
  );
}
