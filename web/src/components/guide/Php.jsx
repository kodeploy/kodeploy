// PHP (Apache + PHP 8, 그누보드 등) 가이드 - 단순 톤.
// KoDeploy는 모든 앱을 비-root(UID 1000)로 실행하므로 Apache가 8080에서 듣고
// 런타임 디렉토리 권한을 열어둬야 함 - 양식이 그 처리를 포함한다.
import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Php() {
  return (
    <>
      <Section title="Dockerfile 양식">
        <p className="text-fg-2 mb-3">
          KoDeploy는 보안상 앱을 <Code>비-root</Code>로 실행해요. 그래서 Apache가 80 대신{" "}
          <Code>8080</Code>에서 듣게 하고 런타임 디렉토리 권한을 열어주는 줄이 필요합니다 -
          아래 양식을 그대로 쓰면 됩니다.
        </p>
        <CodeBlock>
{`FROM php:8.3-apache
# 필요한 확장만 설치 (mysqli/pdo_mysql = MySQL, gd = 이미지 썸네일)
RUN docker-php-ext-install mysqli pdo_mysql
# 비-root 실행 대응 - 8080 리슨 + 런타임 디렉토리 권한
RUN sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf && \\
    sed -i 's/\\*:80/*:8080/' /etc/apache2/sites-available/000-default.conf && \\
    chown -R 1000:1000 /var/www/html /var/run/apache2 \\
      /var/log/apache2 /var/lock/apache2
COPY --chown=1000:1000 . /var/www/html/
EXPOSE 8080`}
        </CodeBlock>
        <p className="kd-t-caption mt-2" style={{ color: "var(--fg-3)" }}>
          배포 폼의 포트는 <Code>8080</Code> 그대로 두세요. GD가 필요하면 확장 설치 줄에{" "}
          <Code>gd</Code>를 추가하면 됩니다.
        </p>
      </Section>

      <Section title="MySQL 쓸 때">
        <p className="text-fg-2 mb-3">
          MySQL을 켜면 접속 정보가 환경변수로 자동 주입돼요. PHP 코드에서{" "}
          <Code>getenv()</Code>로 바로 읽으면 됩니다.
        </p>
        <CodeBlock>
{`<?php
$db = new mysqli(
    getenv('DB_HOST'),       // mysql
    getenv('DB_USER'),       // app
    getenv('DB_PASSWORD'),
    getenv('DB_NAME'),       // app
    (int) getenv('DB_PORT'), // 3306
);`}
        </CodeBlock>
        <p className="kd-t-caption mt-2" style={{ color: "var(--fg-3)" }}>
          그누보드라면 설치 화면의 DB 정보 칸에 위 값들을 그대로 넣으면 됩니다 (호스트{" "}
          <Code>mysql</Code>, DB/사용자 <Code>app</Code>).
        </p>
      </Section>

      <Section title="PostgreSQL 쓸 때">
        <p className="text-fg-2 mb-3">
          같은 <Code>DB_*</Code> 변수가 주입돼요 (호스트만 <Code>postgres</Code>, 포트{" "}
          <Code>5432</Code>). Dockerfile의 확장 설치 줄을 PostgreSQL용으로 바꾸세요:
        </p>
        <CodeBlock>
{`RUN apt-get update && apt-get install -y libpq-dev --no-install-recommends && \\
    docker-php-ext-install pdo_pgsql pgsql`}
        </CodeBlock>
        <div className="my-3">
          <CodeBlock>
{`<?php
$pdo = new PDO(
    sprintf('pgsql:host=%s;port=%s;dbname=%s',
        getenv('DB_HOST'), getenv('DB_PORT'), getenv('DB_NAME')),
    getenv('DB_USER'),
    getenv('DB_PASSWORD'),
);`}
          </CodeBlock>
        </div>
      </Section>

      <Section title="업로드 파일은 영속저장소에">
        <Bullet>
          컨테이너 안에 저장된 파일(업로드 이미지·첨부파일)은{" "}
          <Code>재배포·재시작 때 사라져요</Code>. 게시판처럼 업로드가 있는 앱은
          영속저장소가 필요합니다.
        </Bullet>
        <Bullet>
          배포 폼 <Code>실행 환경 → 스토리지 → 영구 저장소</Code>를 켜고 업로드 디렉토리를
          마운트 경로로 지정하세요 (그누보드 기본값: <Code>/var/www/html/data</Code>).
        </Bullet>
        <Bullet>
          저장소를 꺼도 데이터는 보존돼요 - 다시 켜면 그대로 복원됩니다.
        </Bullet>
      </Section>
    </>
  );
}
