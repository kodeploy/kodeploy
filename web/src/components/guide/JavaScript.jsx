// JavaScript (Node.js 서버 - Express/Nest/Next SSR 등) 가이드 - 단순 톤.
// KoDeploy는 앱을 비-root(UID 1000)로 실행하고, 앱이 process.env.PORT(기본 3000)로
// 바인딩하도록 PORT 환경변수를 자동 주입한다 - 양식이 그 처리를 포함한다.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function JavaScript() {
  return (
    <>
      <Section title="빌드 방식">
        <p className="text-fg-2 mb-3">
          <Code>package.json</Code>이 있으면 별도 Dockerfile 없이 <Code>자동 빌드</Code>가
          가능해요. 배포 폼 빌드 방식을 <Code>자동 감지</Code>으로 두면 의존성 설치와{" "}
          <Code>start</Code> 스크립트를 알아서 잡습니다. 직접 제어하고 싶으면 Dockerfile을
          쓰면 됩니다.
        </p>
        <CodeBlock>
{`{
  "scripts": {
    "start": "node server.js"
  }
}`}
        </CodeBlock>
      </Section>

      <Section title="포트 바인딩 (중요)">
        <p className="text-fg-2 mb-3">
          KoDeploy가 <Code>PORT</Code> 환경변수(기본 <Code>3000</Code>)를 주입해요. 앱은
          배포 설정과 같은 포트에서 연결을 받아야 해요. <Code>process.env.PORT</Code>를 사용하면 설정 변경에도 맞춰 실행됩니다.
        </p>
        <CodeBlock>
{`const express = require("express");
const app = express();

app.get("/", (req, res) => res.send("hello from KoDeploy"));

// 배포 설정의 포트에서 외부 연결을 받습니다.
app.listen(Number(process.env.PORT || 3000), "0.0.0.0");`}
        </CodeBlock>
        <p className="kd-t-caption mt-2" style={{ color: "var(--fg-3)" }}>
          기본 포트는 <Code>3000</Code>이며, 변경하면 PORT에도 변경한 값이 들어와요.
        </p>
      </Section>

      <Section title="MySQL 쓸 때">
        <p className="text-fg-2 mb-3">
          MySQL을 켜면 접속 정보가 <Code>DB_*</Code> 환경변수로 자동 주입돼요.{" "}
          <Code>process.env</Code>로 바로 읽으면 됩니다 (PostgreSQL도 같은 변수, 호스트만{" "}
          <Code>postgres</Code>·포트 <Code>5432</Code>).
        </p>
        <CodeBlock>
{`const mysql = require("mysql2/promise");

const pool = mysql.createPool({
  host: process.env.DB_HOST,          // mysql
  port: Number(process.env.DB_PORT),  // 3306
  user: process.env.DB_USER,          // app
  password: process.env.DB_PASSWORD,
  database: process.env.DB_NAME,      // app
});`}
        </CodeBlock>
      </Section>

      <Section title="Next.js는 어떻게?">
        <Bullet>
          <Code>서버 렌더링(SSR)</Code> 앱이면 이 JavaScript 런타임을 그대로 쓰세요 -{" "}
          <Code>next start</Code>가 결국 <Code>PORT</Code>로 듣는 Node 서버라 자동 빌드가
          그대로 띄웁니다.
        </Bullet>
        <Bullet>
          <Code>정적 export</Code>(<Code>output: "export"</Code>)만 쓸 거면 서버가 필요
          없어요. <Code>프론트엔드 배포</Code> 화면에서 빌드 명령과 결과 폴더를 지정하세요.
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
