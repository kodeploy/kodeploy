import { Link } from "react-router-dom";
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function GettingStarted() {
  return (
    <>
      <Section title="배포할 프로젝트 준비하기">
        <Bullet>KoDeploy는 GitHub 저장소를 빌드해 웹 주소를 제공해요. 계정 하나에 앱 하나를 만들고, 서버와 정적 사이트를 함께 운영할 수 있습니다.</Bullet>
        <Bullet>서버는 Python · Java · PHP · JavaScript를 지원해요. 실행에 필요한 의존성과 시작 명령을 저장소에 포함하세요. Dockerfile이 없어도 자동 빌드를 사용할 수 있어요.</Bullet>
        <Bullet>React · Vue · HTML처럼 빌드한 파일만 제공하는 프로젝트는 <Link to="/guide/static">정적 사이트 배포</Link>를 따라가세요. Next.js에서 서버 렌더링을 사용한다면 JavaScript 서버로 배포합니다.</Bullet>
      </Section>

      <Section title="1. GitHub 저장소 연결">
        <Bullet>GitHub로 로그인한 뒤 <Link to="/deploy">배포 화면</Link>에서 저장소 주소와 브랜치를 선택하세요. 로컬에서 수정한 코드는 선택한 브랜치에 push해야 빌드에 포함됩니다.</Bullet>
        <CodeBlock>{`https://github.com/your-account/your-repository
브랜치: main`}</CodeBlock>
        <Bullet>비공개 저장소는 <Code>GitHub 연결하기</Code>로 GitHub App을 설치하고 해당 저장소의 접근을 허용해야 해요. 로그인과 저장소 접근 연결은 별도입니다.</Bullet>
        <Bullet>저장소나 브랜치를 찾지 못하면 주소, 브랜치 이름, GitHub App에 허용한 저장소를 확인하세요.</Bullet>
      </Section>

      <Section title="2. 실행 환경 선택">
        <Bullet><Code>앱 이름</Code>은 주소에 사용되고 첫 배포에서 정해져요. 비우면 자동으로 만들어집니다.</Bullet>
        <Bullet><Code>자동 감지</Code>는 Dockerfile을 먼저 찾고, 없으면 프로젝트 파일을 바탕으로 빌드해요. 런타임 선택값도 실제 프로젝트와 맞는지 확인하세요.</Bullet>
        <Bullet><Code>포트</Code>를 앱의 실제 실행 포트와 맞추고, 앱이 <Code>0.0.0.0</Code>에서 연결을 받게 하세요. 기본값은 Python 8000, Java·PHP 8080, JavaScript 3000입니다.</Bullet>
        <Bullet>필요한 데이터베이스, Redis, 스토리지와 환경변수를 선택하세요. DB와 저장소를 켜면 연결 정보가 서버 환경변수에 자동으로 들어갑니다.</Bullet>
      </Section>

      <Section title="3. 확인하고 배포">
        <Bullet>마지막 단계에서 설정을 확인하고 배포하세요. 진행 화면에서 빌드와 앱 시작 상태를 확인할 수 있어요.</Bullet>
        <Bullet>배포 후 앱 상세의 <Code>개요</Code>에서 서비스 주소를 열어 동작을 확인하세요. 실패하면 <Code>배포 이력</Code>의 빌드 로그와 <Code>작업 공간 → 터미널·로그</Code>를 확인합니다.</Bullet>
        <Bullet>소스를 수정한 뒤에는 변경사항을 GitHub에 push하고 다시 배포하세요. 포트·런타임·DB 등 실행 설정도 재배포 화면에서 변경합니다.</Bullet>
      </Section>

      <Section title="서버와 프론트엔드 주소">
        <CodeBlock>{`서버 API     https://{앱이름}-api.kodeploy.com
기본 주소    https://{앱이름}.kodeploy.com`}</CodeBlock>
        <Bullet>정적 사이트가 있으면 기본 주소는 정적 사이트로, 없으면 서버로 연결돼요. 서버의 <Code>-api</Code> 주소는 정적 사이트를 추가해도 유지됩니다.</Bullet>
        <Bullet>프론트엔드에서 서버를 호출할 때는 HTTPS의 <Code>-api</Code> 주소를 사용하세요. 서버에서 CORS를 설정한다면 프론트엔드의 실제 접속 주소를 허용해야 합니다.</Bullet>
      </Section>
    </>
  );
}
