// 문제 해결 가이드 - 사용자가 실제로 마주치는 실패 메시지 기준으로 역인덱싱.
// 에러 문구("Pod 시작 실패 (타임아웃)" 등)는 백엔드 service.py가 내는 문자열과 일치시킬 것.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Troubleshooting() {
  return (
    <>
      <Section title="빌드 실패">
        <Bullet>
          앱 상세의 배포 이력에서 실패한 빌드를 선택하면 <Code>빌드 로그</Code> 전체가 보여요 -
          처음 오류가 발생한 부분과 마지막 부분을 함께 확인하세요.
        </Bullet>
        <Bullet>
          <Code>clone 실패</Code>: 저장소 주소·브랜치 이름 오타이거나, private 저장소인데
          GitHub 연동(설치)을 안 한 경우예요.
        </Bullet>
        <Bullet>
          자동 빌드(Nixpacks)가 프로젝트를 못 알아보면: <Code>requirements.txt</Code>·
          <Code>pom.xml</Code>·<Code>composer.json</Code> 같은 파일이 저장소에 있는지,
          자동 감지가 올바른 폴더를 선택했는지 확인하세요. 원하는 프로젝트가 감지되지 않으면 Dockerfile을 작성하고 그 경로를 지정할 수 있어요.
        </Bullet>
      </Section>

      <Section title={'"Pod 시작 실패 (타임아웃)"이 떴어요'}>
        <p className="text-fg-2 mb-3">
          빌드 후 앱이 제한 시간 안에 준비되지 않은 상태예요. 포트·실행 오류부터 확인하고, 이미지 다운로드나 디스크 연결 문제도 살펴보세요.
        </p>
        <Bullet>
          <Code>포트 불일치</Code> - 배포 폼의 포트와 앱이 실제로 듣는 포트가 다른 경우.
          FastAPI 기본 8000, Spring 기본 8080.
        </Bullet>
        <Bullet>
          <Code>127.0.0.1 바인딩</Code> - 앱이 localhost에서만 들으면 밖에서 접근이 안 돼요.
          반드시 <Code>0.0.0.0</Code>으로 바인딩하세요:
        </Bullet>
        <div className="my-3">
          <CodeBlock>{`uvicorn main:app --host 0.0.0.0 --port 8000   # Python
java -jar app.jar                              # Spring은 기본 0.0.0.0`}</CodeBlock>
        </div>
        <Bullet>
          <Code>시작 직후 크래시</Code> - DB 접속 실패·환경변수 누락 등으로 프로세스가 바로
          죽는 경우. <Code>작업 공간 → 터미널·로그</Code>에서 에러를 확인하세요.
        </Bullet>
      </Section>

      <Section title="앱이 자꾸 재시작돼요 (crashing)">
        <p className="text-fg-2 mb-3">
          가장 흔한 원인은 <Code>메모리 한도 초과(OOM)</Code>예요. 런타임별 메모리 한도:
        </p>
        <div className="my-3">
          <CodeBlock>{`Python       600Mi
Java         1Gi
PHP          768Mi
JavaScript   640Mi
정적          64Mi (nginx)`}</CodeBlock>
        </div>
        <Bullet>
          작업 공간의 <Code>모니터링</Code>에서 메모리 그래프가 한도에 닿는지 확인하세요.
        </Bullet>
        <Bullet>
          <Code>터미널·로그</Code>에서 이전 인스턴스(죽기 직전) 로그를 선택할 수 있어요 -
          크래시 원인은 보통 거기 있습니다.
        </Bullet>
        <Bullet>
          Java는 JVM heap이 한도의 75%로 자동 설정돼요(별도 <Code>-Xmx</Code> 불필요).
          메모리 한도에는 heap 외의 메모리도 포함되므로, JVM 오류와 실제 메모리 사용량을 함께 확인하세요.
        </Bullet>
      </Section>

      <Section title="배포는 됐는데 페이지가 안 열려요">
        <Bullet>
          첫 배포 직후엔 이미지 받기·부팅에 1~2분 걸릴 수 있어요. 상단의 앱 상태가{" "}
          <Code>running</Code>이 될 때까지 기다려 보세요.
        </Bullet>
        <Bullet>
          정적 사이트가 켜져 있으면 <Code>{"{앱}"}.kodeploy.com</Code>은 정적 사이트,
          서버는 <Code>{"{앱}"}-api.kodeploy.com</Code>이에요 - 주소를 헷갈리지 않았는지
          확인하세요.
        </Bullet>
        <Bullet>
          그래도 안 되면 <Code>소통</Code> 탭에 글을 남겨주세요 - 운영자가 확인합니다.
        </Bullet>
      </Section>
    </>
  );
}
