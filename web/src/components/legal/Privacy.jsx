// 개인정보처리방침 — 실제로 수집·보관하는 항목(core 코드 기준)을 그대로 적는다.
// 항목이 바뀌면 이 문서도 같이 고친다: 로그인(auth/service.py), 세션(auth/model.py UserSession),
// 앱·빌드 데이터(deploy/model.py), 삭제 범위(deploy/status.py delete_app, auth/account.py), 백업 보존(deploy/k8s/backup).
import { Bullet, Section } from "../guide/atoms.jsx";
import LegalPage, { CONTACT_EMAIL } from "./LegalPage.jsx";

export default function Privacy() {
  return (
    <LegalPage title="개인정보처리방침">
      <p className="mb-9">
        KoDeploy(이하 "서비스")는 GitHub 저장소를 연결해 앱을 배포하고 운영하는 서비스입니다. 서비스를 운영하는
        개인 운영자(이하 "운영자")는 이용자의 개인정보를 아래와 같이 처리합니다.
      </p>

      <Section title="1. 수집하는 항목과 방법">
        <p className="mb-2">GitHub 로그인 때 GitHub에서 받는 정보</p>
        <Bullet>GitHub 고유 ID, GitHub 아이디(login), 프로필 사진 주소</Bullet>
        <Bullet>이메일 주소 (GitHub 계정의 대표 인증 이메일)</Bullet>
        <Bullet>GitHub App 설치 번호 (비공개 저장소를 받아 빌드할 때만 사용)</Bullet>
        <p className="mt-4 mb-2">서비스를 이용하면서 생기는 정보</p>
        <Bullet>로그인 세션 정보: 세션 식별자, 접속 IP, 브라우저 정보, 만료 시각</Bullet>
        <Bullet>배포 정보: 저장소 주소, 브랜치, 앱 이름과 설정, 빌드·실행 로그, 배포 시각과 결과</Bullet>
        <Bullet>환경변수 (이용자가 직접 입력한 값 — 비밀 값이 포함될 수 있습니다)</Bullet>
        <Bullet>이용자의 앱이 저장하는 데이터 (데이터베이스, 영구 저장소, 오브젝트 스토리지 파일)</Bullet>
        <Bullet>연결한 도메인, 커뮤니티에 쓴 글과 댓글, 앱 공유를 위해 입력한 초대 대상(이메일 또는 GitHub 아이디)</Bullet>
      </Section>

      <Section title="2. 이용 목적">
        <Bullet>회원 식별과 로그인 유지, 앱 소유자 확인</Bullet>
        <Bullet>앱 빌드·배포·운영과 장애 대응, 이용 한도(앱 개수 등급) 관리</Bullet>
        <Bullet>앱 공유 초대와 권한 관리</Bullet>
        <Bullet>빌드·배포 실패 원인을 자동으로 분석해 보여 주는 진단 기능</Bullet>
        <Bullet>서비스 품질 개선을 위한 통계 (빌드 시간, 성공·실패 횟수)</Bullet>
      </Section>

      <Section title="3. 보유 기간과 파기">
        <p className="mb-2">
          이용자가 앱을 삭제하거나 회원 탈퇴를 하면 아래 정보는 곧바로 삭제합니다.
        </p>
        <Bullet>앱의 실행 환경(서버, 데이터베이스와 그 데이터, 영구 저장소, 환경변수)과 오브젝트 스토리지 파일</Bullet>
        <Bullet>연결한 커스텀 도메인 설정, 배포 이력과 빌드·실행 로그, 저장한 DB 쿼리</Bullet>
        <p className="mt-4 mb-2">회원 탈퇴 때는 위에 더해 다음도 삭제합니다.</p>
        <Bullet>계정 정보(GitHub ID, 아이디, 이메일, 프로필 사진 주소), 로그인 세션</Bullet>
        <Bullet>커뮤니티 글과 댓글, 공유 멤버십과 주고받은 초대, 빌드 운영 기록</Bullet>
        <p className="mt-4 mb-2">즉시 지워지지 않는 것</p>
        <Bullet>
          운영 백업: 데이터베이스는 매일 백업하며 30일이 지나면 자동으로 삭제합니다. 삭제한 정보가 그동안 백업에
          남아 있을 수 있습니다.
        </Bullet>
        <Bullet>
          빌드된 컨테이너 이미지: 앱을 삭제해도 이미지 저장소에 남아 있을 수 있으며 정리 대상입니다.
        </Bullet>
        <Bullet>
          앱 삭제 후 회원 탈퇴 전까지: 앱 이름, 런타임, 빌드 시간과 결과 같은 운영 기록을 서비스 운영과 통계를
          위해 보관합니다. 회원 탈퇴 때 삭제합니다.
        </Bullet>
        <Bullet>
          배포 설정 저장소의 변경 이력: 앱 이름, 주소, 내부 식별자 같은 배포 설정 이력이 남을 수 있습니다.
          환경변수와 비밀 값은 담기지 않습니다.
        </Bullet>
        <Bullet>
          운영자 조치 기록: 운영자가 이용자의 앱이나 계정에 조치한 기록(앱 이름, GitHub 아이디, 조치 내용, 시각)은
          운영 확인을 위해 보관합니다.
        </Bullet>
        <Bullet>관계 법령에 따라 보관해야 하는 정보는 해당 기간 동안 보관합니다.</Bullet>
      </Section>

      <Section title="4. 처리 위탁과 국외 이전">
        <p className="mb-2">서비스 제공을 위해 아래 사업자의 서비스를 이용하며, 그 과정에서 정보가 처리됩니다.</p>
        <Bullet>GitHub: 로그인, 저장소 접근</Bullet>
        <Bullet>Cloudflare: 웹 사이트와 도메인 연결, 오브젝트 스토리지, 트래픽 전달</Bullet>
        <Bullet>Oracle Cloud Infrastructure: 서버 호스팅</Bullet>
        <Bullet>
          외부 AI 모델 API: 빌드·배포가 실패했을 때 원인을 분석하기 위해 해당 빌드 로그와 앱 로그의 일부가 전달됩니다.
          로그에 비밀 값이 찍히지 않도록 주의해 주세요.
        </Bullet>
        <p className="mt-3">위 사업자의 서버는 국외에 있을 수 있습니다. 운영자는 위 목적 밖으로 개인정보를 제3자에게 제공하지 않습니다.</p>
      </Section>

      <Section title="5. 이용자의 권리">
        <Bullet>언제든지 본인의 정보를 조회하고, 앱을 삭제하고, 회원 탈퇴로 모든 정보를 삭제할 수 있습니다.</Bullet>
        <Bullet>회원 탈퇴는 화면 오른쪽 위 프로필 메뉴의 "회원 탈퇴"에서 직접 할 수 있습니다.</Bullet>
        <Bullet>
          GitHub와의 연결은 GitHub 설정의 Applications에서 KoDeploy를 제거하면 끊을 수 있습니다. 서비스는
          GitHub 쪽 설치를 대신 해제하지 못합니다.
        </Bullet>
        <Bullet>열람, 정정, 삭제 요청은 아래 연락처로 보내 주세요.</Bullet>
      </Section>

      <Section title="6. 쿠키">
        <p>
          로그인 상태를 유지하기 위해 세션 쿠키 1개를 사용합니다. 광고나 추적 목적의 쿠키는 사용하지 않습니다.
          브라우저에서 쿠키를 차단하면 로그인할 수 없습니다.
        </p>
      </Section>

      <Section title="7. 안전성 확보 조치">
        <Bullet>전송 구간 암호화(HTTPS)와 로그인 세션의 서버 측 관리</Bullet>
        <Bullet>이용자별 실행 환경 분리, 앱 소유자와 초대한 멤버만 접근할 수 있는 접근 통제</Bullet>
        <Bullet>
          운영자는 장애 대응과 서비스 운영을 위해 이용자의 앱(설정, 로그, 실행 환경, 데이터베이스)에 접근할 수
          있습니다. 앱을 바꾸거나 터미널에 접속한 조치는 기록으로 남깁니다.
        </Bullet>
        <Bullet>환경변수 같은 비밀 값은 클러스터의 암호화된 저장소(Secret)에 보관</Bullet>
      </Section>

      <Section title="8. 만 14세 미만 이용자">
        <p>
          만 14세 미만의 이용자는 법정대리인의 동의를 받아 서비스를 이용해야 하며, 그 동의에 대한 책임은 이용자와
          법정대리인에게 있습니다. 서비스는 GitHub 계정으로 로그인하므로 GitHub의 이용 연령 기준도 함께 적용됩니다.
        </p>
      </Section>

      <Section title="9. 개인정보 관련 문의">
        <p>
          개인정보 보호 책임자: 서비스 운영자
          <br />
          문의: <a href={`mailto:${CONTACT_EMAIL}`} style={{ color: "var(--accent)", textDecoration: "underline" }}>{CONTACT_EMAIL}</a>
        </p>
      </Section>

      <Section title="10. 방침의 변경">
        <p>이 방침을 바꾸면 시행일과 함께 이 페이지에 게시합니다. 이용자에게 불리한 변경은 시행 7일 전에 알립니다.</p>
      </Section>
    </LegalPage>
  );
}
