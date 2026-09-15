import { Bullet, Code, Section } from "./atoms.jsx";

export default function Workspace() {
  return (
    <>
      <Section title="터미널 열기">
        <Bullet>앱 상세의 <Code>작업 공간 → 터미널·로그</Code>에서 실행 중인 앱 서버에 접속할 수 있어요. DB가 있으면 DB 터미널, Redis를 켰다면 Redis 터미널도 선택할 수 있습니다.</Bullet>
        <Bullet>터미널은 실제 실행 중인 컨테이너에 연결돼요. 앱이 시작되지 않거나 이미지에 셸이 없다면 접속하지 못할 수 있습니다.</Bullet>
        <Bullet>터미널에서 수정한 앱 파일은 다음 배포에 반영되지 않아요. 지속할 코드 변경은 GitHub 저장소에 반영하고, 유지할 파일은 영구 저장소에 저장하세요.</Bullet>
      </Section>

      <Section title="로그 확인하기">
        <Bullet><Code>배포 이력</Code>에서 빌드를 선택하면 의존성 설치·컴파일 등 빌드 과정을 볼 수 있어요. 빌드가 실패했다면 오류가 처음 나온 부분과 마지막 부분을 함께 확인하세요.</Bullet>
        <Bullet><Code>작업 공간 → 터미널·로그</Code>에서는 실행 중인 앱의 출력을 확인해요. 앱이 로그를 표준 출력·표준 에러로 기록해야 이곳에 표시됩니다.</Bullet>
        <Bullet>앱이 재시작을 반복하면 로그의 <Code>이전 인스턴스</Code>를 선택해 종료 직전 오류를 확인하세요. 이전 로그가 남아 있을 때 조회할 수 있습니다.</Bullet>
        <Bullet>빌드가 성공해도 앱 실행 중 오류가 날 수 있어요. DB 연결 실패·누락된 환경변수·포트 오류는 실행 로그에서 확인합니다.</Bullet>
      </Section>

      <Section title="모니터링과 스토리지">
        <Bullet><Code>모니터링</Code>에서 CPU·메모리 사용량을 확인하세요. 메모리 한도 근처에서 재시작이 반복된다면 메모리 부족 여부를 실행 로그와 함께 살펴보세요.</Bullet>
        <Bullet>객체 스토리지를 사용하는 앱은 <Code>스토리지</Code> 탭에서 파일 목록·미리보기·URL 복사·삭제를 사용할 수 있어요. 영구 저장소의 로컬 파일은 앱 서버 터미널에서 확인합니다.</Bullet>
      </Section>

      <Section title="배포 이력 활용하기">
        <Bullet>빌드별 결과와 설정, 로그를 비교해 어느 변경 이후 문제가 생겼는지 확인하세요. 환경변수 변경도 이력에 기록됩니다.</Bullet>
        <Bullet>이전 소스로 돌아가려면 GitHub에서 원하는 코드 상태를 배포할 브랜치에 반영한 뒤 재배포하세요. 현재 이력 화면에는 이전 이미지를 바로 복원하는 롤백 기능이 없습니다.</Bullet>
      </Section>
    </>
  );
}
