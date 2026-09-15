import { Bullet, Code, CodeBlock, Section } from "./atoms.jsx";

export default function Database() {
  return (
    <>
      <Section title="데이터베이스 연결">
        <Bullet>서버 배포의 <Code>실행 환경 → 데이터베이스</Code>에서 MySQL 8.4 또는 PostgreSQL 16 중 하나를 선택하세요. 한 앱에는 한 종류의 DB를 연결할 수 있습니다.</Bullet>
        <Bullet>서버에 아래 연결 정보가 자동으로 주입돼요. DB는 앱에서 내부 호스트 이름으로 연결하며, 브라우저의 정적 코드에서 직접 연결하지 않습니다.</Bullet>
        <CodeBlock>{`DB_HOST=mysql       # PostgreSQL: postgres
DB_PORT=3306        # PostgreSQL: 5432
DB_NAME=app
DB_USER=app
DB_PASSWORD=자동으로 생성된 값`}</CodeBlock>
        <Bullet>Python SQLAlchemy용 <Code>DATABASE_URL</Code>과 Spring용 <Code>SPRING_DATASOURCE_*</Code>도 함께 제공해요. Node.js·PHP나 다른 드라이버를 쓴다면 위의 개별 변수로 연결하세요.</Bullet>
      </Section>

      <Section title="SQL 실행과 DB 터미널">
        <Bullet>앱 상세의 <Code>작업 공간 → 데이터베이스</Code>에서 SQL을 실행하고 결과를 표로 볼 수 있어요. 조회 결과는 한 번에 최대 500행이며, 지원되는 조회는 다음 페이지를 불러올 수 있습니다.</Bullet>
        <CodeBlock>{`SELECT 1 AS connection_ok;`}</CodeBlock>
        <Bullet>표의 쿼리는 실행할 때마다 별도 연결을 사용해요. 트랜잭션이나 세션 상태를 이어서 사용하려면 같은 화면의 <Code>터미널</Code>을 사용하세요.</Bullet>
        <Bullet>INSERT·UPDATE·DELETE 같은 SQL은 실제 DB에 적용됩니다. 대량 변경 전에는 스냅샷을 내보내두세요.</Bullet>
      </Section>

      <Section title="데이터 가져오기와 보존">
        <Bullet>배포할 때 <Code>초기 데이터</Code>에서 덤프를 넣거나, 배포 후 <Code>설정 → 스토리지</Code>에서 DB 스냅샷 내보내기·복원을 사용할 수 있어요. 사용 중인 DB 종류에 맞는 <Code>.sql</Code> 또는 <Code>.sql.gz</Code> 파일을 준비하세요. 복원은 기존 데이터를 덮어쓸 수 있으므로 먼저 내보내두세요.</Bullet>
        <Bullet>DB를 <Code>사용 안 함</Code>으로 바꿔도 디스크의 데이터는 남고, 같은 종류를 다시 켜면 재사용해요. DB 종류를 바꾸는 것은 데이터 변환이나 이전을 수행하지 않습니다.</Bullet>
        <Bullet>앱을 삭제하면 DB 데이터도 삭제됩니다. 보관할 데이터는 삭제 전에 내보내세요.</Bullet>
      </Section>

      <Section title="Redis 사용">
        <Bullet><Code>Redis 사용</Code>을 선택하면 DB와 함께 캐시를 붙일 수 있어요. 연결 정보는 서버에 자동 주입됩니다.</Bullet>
        <CodeBlock>{`REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=자동으로 생성된 값
REDIS_URL=redis://:비밀번호@redis:6379/0`}</CodeBlock>
        <Bullet>Spring용 <Code>SPRING_DATA_REDIS_*</Code>도 제공해요. <Code>작업 공간 → 터미널·로그</Code>에서 Redis 터미널을 열 수 있습니다.</Bullet>
        <Bullet>Redis에는 영속 디스크가 없어요. 재시작이나 사용 해제 시 데이터가 사라질 수 있으므로 재생성 가능한 캐시를 저장하세요. 캐시 메모리 상한은 128MB이며, 가득 차면 최근 사용이 적은 키부터 제거합니다.</Bullet>
      </Section>
    </>
  );
}
