import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Environment() {
  return (
    <>
      <Section title="서버 실행 환경변수">
        <Bullet>첫 배포에서는 <Code>실행 환경 → 환경변수 추가</Code>, 배포 후에는 앱 상세의 <Code>환경변수</Code> 탭에서 이름과 값을 입력하세요. 외부 API 키나 앱 설정을 코드에 넣지 않고 전달할 수 있어요.</Bullet>
        <CodeBlock>{`APP_ENV=production
EXTERNAL_API_KEY=your-api-key`}</CodeBlock>
        <Bullet>앱에서는 Python의 <Code>os.environ</Code>, Node.js의 <Code>process.env</Code>, PHP의 <Code>getenv()</Code> 등으로 읽습니다.</Bullet>
        <Bullet><Code>저장 및 재배포</Code>를 누르면 새 값으로 서버를 다시 시작해요. 이 작업은 소스를 다시 빌드하지 않으며, 변경 기록은 <Code>배포 이력</Code>에서 확인할 수 있어요.</Bullet>
      </Section>

      <Section title="이름과 값의 규칙">
        <Bullet>이름은 영문 대문자 또는 밑줄로 시작하고, 이후에는 대문자·숫자·밑줄을 쓸 수 있어요. 예: <Code>API_KEY</Code>, <Code>APP_ENV</Code>.</Bullet>
        <Bullet>최대 50개, 값 하나당 최대 4,096자입니다. 화면에서 행을 삭제하고 저장하면 해당 변수도 삭제돼요.</Bullet>
        <Bullet><Code>PYTHONUNBUFFERED</Code>와 <Code>JAVA_TOOL_OPTIONS</Code>는 플랫폼 예약값입니다. DB·Redis·객체 스토리지를 켰다면 화면의 자동 주입 변수 목록을 확인하고 같은 이름을 직접 추가하지 마세요.</Bullet>
        <Bullet>JavaScript 서버의 <Code>PORT</Code>는 배포 설정의 포트로 주입돼요. 포트를 바꾸려면 재배포 화면에서 변경하세요.</Bullet>
      </Section>

      <Section title="정적 사이트의 빌드 환경변수">
        <Bullet>정적 사이트는 <Code>프론트엔드 배포 → 빌드 환경변수</Code>에서 설정해요. 서버 환경변수와 별개이며, 값 변경 후 프론트엔드를 다시 빌드해야 반영됩니다.</Bullet>
        <CodeBlock>{`VITE_API_URL=https://{앱이름}-api.kodeploy.com`}</CodeBlock>
        <Bullet>Vite 앱에서는 <Code>import.meta.env.VITE_API_URL</Code>로 읽을 수 있어요. 사용하는 프레임워크가 요구하는 변수 이름을 맞춰주세요.</Bullet>
        <Bullet>이 값은 빌드 결과에 포함되어 브라우저에 공개됩니다. 공개 API 주소처럼 사용자에게 보여도 되는 값만 넣고, DB 비밀번호나 비밀 API 키는 서버 환경변수로 관리하세요.</Bullet>
      </Section>
    </>
  );
}
