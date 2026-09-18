// SQL 색칠용 토크나이저 — 파서가 아니라 "눈에 띄어야 할 것"만 가른다.
//
// 규칙은 셋뿐이다: 키워드 / 리터럴(숫자·문자열) / 주석. 함수명(COUNT, SUM …)이나 연산자는
// 칠하지 않는다 — 시안의 편집기가 그렇고, 모노크롬 위에서 색이 늘어날수록 "어디를 봐야 하나"가
// 흐려진다. 색은 문장의 뼈대(키워드)와 눈으로 확인할 값(리터럴)에만 쓴다.
//
// 방언: 작은따옴표만 문자열로 본다. 큰따옴표는 Postgres에서, 백틱은 MySQL에서 **식별자**라
// 컬럼명을 문자열색으로 칠하면 거짓말이 된다. 두 방언을 다 받는 콘솔이라 겹치지 않는 쪽만 칠한다.

const KEYWORDS = new Set(
  `SELECT FROM WHERE GROUP BY ORDER HAVING LIMIT OFFSET
   JOIN INNER LEFT RIGHT FULL OUTER CROSS ON USING AS
   AND OR NOT IN IS LIKE ILIKE BETWEEN EXISTS ANY
   UNION INTERSECT EXCEPT ALL DISTINCT
   INSERT INTO VALUES UPDATE SET DELETE REPLACE TRUNCATE
   CREATE ALTER DROP RENAME ADD COLUMN TABLE INDEX VIEW DATABASE SCHEMA
   PRIMARY KEY FOREIGN REFERENCES UNIQUE DEFAULT CONSTRAINT AUTO_INCREMENT
   CASE WHEN THEN ELSE END IF WITH RECURSIVE
   ASC DESC NULL TRUE FALSE
   SHOW DESCRIBE DESC EXPLAIN USE GRANT REVOKE
   BEGIN COMMIT ROLLBACK`.split(/\s+/),
);

// 한 번의 스캔으로 주석 → 문자열 → 인용식별자 → 숫자 → 낱말 순으로 집는다. 순서가 곧
// 우선순위다: 주석 안의 SELECT, 문자열 안의 숫자, `select`라는 이름의 컬럼이 따로 칠해지면 안 된다.
// 인용식별자를 굳이 집는 이유가 그 마지막 경우다 — 안 집으면 백틱 안의 예약어가 키워드로 칠해진다.
// 닫히지 않은 따옴표('abc)도 끝까지 그 토큰으로 본다 — 타이핑 도중의 정상 상태라 깜빡이면 안 된다.
const TOKEN_RE =
  /(--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|('(?:[^']|'')*'?)|(`(?:[^`]|``)*`?|"(?:[^"]|"")*"?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)/g;

// [{ kind: "text" | "keyword" | "literal" | "comment", text }] — 원문을 순서대로 빠짐없이 덮는다
// (조각을 이어 붙이면 입력과 정확히 같은 문자열이 된다. 아니면 색 레이어가 글자와 어긋난다).
export function tokenizeSql(sql) {
  const src = sql || "";
  const out = [];
  let last = 0;
  let m;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(src)) !== null) {
    if (m.index > last) out.push({ kind: "text", text: src.slice(last, m.index) });
    const [text, comment, string, quotedIdent, number, word] = m;
    if (comment) out.push({ kind: "comment", text });
    else if (string || number) out.push({ kind: "literal", text });
    else if (quotedIdent) out.push({ kind: "text", text });   // 인용식별자 — 안을 들여다보지 않는다
    else if (word) {
      out.push({ kind: KEYWORDS.has(word.toUpperCase()) ? "keyword" : "text", text });
    }
    last = m.index + text.length;
  }
  if (last < src.length) out.push({ kind: "text", text: src.slice(last) });
  return out;
}
