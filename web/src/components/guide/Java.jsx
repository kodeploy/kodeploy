// Java (Spring Boot + 선택적 DB) 가이드 - 단순 톤.
import { Code, CodeBlock, Section } from "./atoms.jsx";

export default function Java() {
  return (
    <>
      <Section title="Dockerfile 양식">
        <CodeBlock>
{`FROM eclipse-temurin:17-jdk AS build
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN apt-get update && apt-get install -y maven --no-install-recommends && \\
    mvn clean package -DskipTests

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]`}
        </CodeBlock>
        <p className="kd-t-caption mt-2" style={{ color: "var(--fg-3)" }}>
          repo에 <Code>mvnw</Code>가 있으면 Maven 설치 줄을 빼고{" "}
          <Code>./mvnw clean package -DskipTests</Code>로 바꿔도 됩니다.
        </p>
      </Section>

      <Section title="DB 쓸 때 - 의존성 1줄만 추가">
        <p className="text-fg-2 mb-3">
          <Code>pom.xml</Code>에 드라이버만 추가하면{" "}
          <strong style={{ color: "var(--fg-1)", fontWeight: 600 }}>코드 변경 0</strong>으로 DB 연결 됩니다.
          접속 정보(<Code>SPRING_DATASOURCE_*</Code>)가 자동 주입되고 Spring Boot가
          알아서 인식해요. MySQL·PostgreSQL 동일합니다.
        </p>
        <CodeBlock>
{`<!-- MySQL -->
<dependency>
    <groupId>com.mysql</groupId>
    <artifactId>mysql-connector-j</artifactId>
    <scope>runtime</scope>
</dependency>

<!-- PostgreSQL이면 이걸로 -->
<dependency>
    <groupId>org.postgresql</groupId>
    <artifactId>postgresql</artifactId>
    <scope>runtime</scope>
</dependency>`}
        </CodeBlock>
      </Section>
    </>
  );
}
