// 기본 규칙 섹션 - 다른 페이지에서도 import 가능하게 default export.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Basics() {
  return (
    <>
      <Section title="Dockerfile은 없어도 됩니다">
        <Bullet>
          빌드 방식 기본값(<Code>자동 감지</Code>)은 자동 감지예요 - repo에{" "}
          <strong style={{ color: "var(--fg-1)", fontWeight: 600 }}>Dockerfile이 있으면 그걸로</strong>, 없으면{" "}
          <strong style={{ color: "var(--fg-1)", fontWeight: 600 }}>프로젝트를 분석해 자동으로 빌드</strong>합니다
          (requirements.txt · pom.xml · composer.json 등을 보고 판단).
        </Bullet>
        <Bullet>
          빌드를 직접 제어하고 싶을 때만 Dockerfile을 두세요. repo 루트가 아니면 배포 폼의{" "}
          <Code>Dockerfile 경로</Code>에 위치를 입력하면 됩니다 - 런타임별 권장 양식은
          런타임별 문서(Python · Java · PHP · JavaScript)에서 확인하세요.
        </Bullet>
      </Section>

      <Section title="딱 2가지만 챙기면 끝">
        <Bullet>
          <strong style={{ color: "var(--fg-1)", fontWeight: 600 }}>앱이 열어둘 포트</strong> - 폼이 런타임 선택 시
          기본값을 채워줘요 (FastAPI 8000, Spring·PHP 8080, JavaScript 3000). 앱이 다른 포트면 수정.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--fg-1)", fontWeight: 600 }}>0.0.0.0 바인딩</strong> - localhost(127.0.0.1)에서만
          들으면 밖에서 접근이 안 돼요. 빌드는 KoDeploy가 알아서 하니 로컬에서 미리
          mvn / npm 빌드는 안 해도 됩니다.
        </Bullet>
      </Section>

      <Section title="하위 폴더와 파일 권한">
        <Bullet>자동 감지는 저장소의 Dockerfile과 프로젝트 파일을 찾아요. 여러 프로젝트가 있다면 선택된 빌드 대상이 맞는지 빌드 로그를 확인하세요.</Bullet>
        <Bullet><Code>Dockerfile 사용</Code>에서 <Code>backend/Dockerfile</Code>을 지정하면 <Code>backend</Code>가 빌드 기준 폴더가 돼요. Dockerfile의 <Code>COPY</Code> 경로도 그 폴더 기준이며, 상위 폴더의 파일을 가져올 수 없습니다.</Bullet>
        <Bullet>서버 앱은 비-root 사용자(UID 1000)로 실행돼요. Dockerfile에서 앱이 써야 하는 폴더의 권한을 준비하고, 실행 중 root 권한이 필요한 명령을 사용하지 않도록 구성하세요.</Bullet>
      </Section>

      <Section title="DB를 켜면 이런 환경변수가 자동으로 들어와요">
        <p className="text-fg-2 mb-3">
          MySQL·PostgreSQL 어느 쪽을 켜도 같은 이름의 변수가 주입돼요 - 앱 코드에서
          그대로 가져다 쓰면 DB 접속이 됩니다.
        </p>
        <CodeBlock>
          {[
            "DB_HOST=mysql              # postgres면 postgres",
            "DB_PORT=3306               # postgres면 5432",
            "DB_NAME=app",
            "DB_USER=app",
            "DB_PASSWORD=...",
            "",
            "# 완성된 접속 문자열도 함께 들어와요",
            "DATABASE_URL=mysql+pymysql://app:...@mysql:3306/app",
            "SPRING_DATASOURCE_URL=...  # Spring Boot가 자동 인식",
          ].join("\n")}
        </CodeBlock>
        <p className="kd-t-caption mt-2" style={{ color: "var(--fg-3)" }}>
          Python은 <Code>DATABASE_URL</Code> 한 줄, Spring은 의존성만 추가하면 끝이에요 -
          자세한 건 각 런타임 탭에서.
        </p>
      </Section>
    </>
  );
}
