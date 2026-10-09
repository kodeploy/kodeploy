// DeployForm 옆에 fixed drawer로 뜨는 가이드 패널.
// TopBar 아래(60px)부터 화면 끝까지 차지. 자체 스크롤.
// view 키(python/java/php/static/storage/custom-domain)에 따라 해당 가이드만 보여줌. 닫기 X 버튼.
// 동일 가이드 컨텐츠가 /guide 페이지에서도 재사용됨.
import { X } from "lucide-react";
import CustomDomain from "./guide/CustomDomain.jsx";
import Go from "./guide/Go.jsx";
import Java from "./guide/Java.jsx";
import JavaScript from "./guide/JavaScript.jsx";
import Php from "./guide/Php.jsx";
import Python from "./guide/Python.jsx";
import Static from "./guide/Static.jsx";
import Storage from "./guide/Storage.jsx";

const GUIDES = {
  python: { title: "Python 가이드", Component: Python },
  java: { title: "Java 가이드", Component: Java },
  php: { title: "PHP 가이드", Component: Php },
  javascript: { title: "JavaScript 가이드", Component: JavaScript },
  go: { title: "Go 가이드", Component: Go },
  static: { title: "정적 사이트 가이드", Component: Static },
  storage: { title: "저장소 가이드", Component: Storage },
  "custom-domain": { title: "커스텀 도메인 가이드", Component: CustomDomain },
};

export default function GuidePanel({ runtime, onClose }) {
  const guide = GUIDES[runtime] || GUIDES.python;
  const Body = guide.Component;
  return (
    <aside
      className="kd-slide-in-right fixed overflow-auto scroll-thin"
      style={{
        top: 60,                  // TopBar 높이
        bottom: 0,                // 화면 끝까지
        right: 0,
        width: 520,
        zIndex: 20,               // BuildListWidget(z-30)보다 낮게 — widget 위에 보임
        background: "var(--kd-panel)",
        borderLeft: "1px solid var(--line-2)",
        padding: "20px 24px",
      }}
    >
      <div className="flex items-center justify-between mb-5">
        <h3
          className="text-[15px] text-fg-1"
          style={{ fontWeight: 590, letterSpacing: -0.2 }}
        >
          {guide.title}
        </h3>
        <button
          onClick={onClose}
          className="w-7 h-7 rounded-md text-fg-4 hover:text-fg-1 hover:bg-[var(--line-1)] transition-colors flex items-center justify-center"
          aria-label="가이드 닫기"
        >
          <X size={14} strokeWidth={1.8} />
        </button>
      </div>
      <Body />
    </aside>
  );
}
