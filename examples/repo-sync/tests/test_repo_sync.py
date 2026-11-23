from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import repo_sync as rs  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)


class FakeUploader:
    def __init__(self) -> None:
        self.docs: dict[str, tuple[str, bytes, list[str]]] = {}
        self.deleted: list[str] = []
        self.count = 0

    def upload(self, title: str, payload: bytes, tags: list[str]) -> str:
        self.count += 1
        document_id = f"d{self.count}"
        self.docs[document_id] = (title, payload, tags)
        return document_id

    def delete(self, document_id: str) -> None:
        self.deleted.append(document_id)
        self.docs.pop(document_id, None)

    def titles(self) -> list[str]:
        return sorted(title for title, _, _ in self.docs.values())


JAVA = """package demo.api;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    @GetMapping("/{id}")
    public Order get(String id) { return null; }

    @PostMapping
    public Order create(Order order) { return null; }
}
"""

PY = """from fastapi import FastAPI
app = FastAPI()

class Health:
    pass

@app.get("/health")
def health():
    return {}
"""


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "shop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "docs/설계.md", "# 주문 설계\n\n주문은 결제 후 확정한다.\n")
    write(repo, "README.md", "# shop\n")
    write(repo, "server/src/OrderController.java", JAVA)
    write(repo, "tools/app.py", PY)
    write(repo, "server/node_modules/x/README.md", "vendored")
    commit(repo, "first")
    return repo


def args_for(repo: Path, tmp_path: Path, *extra: str):
    return rs.parse_args(["--repo", str(repo), "--ref", "main", "--name", "shop", "--state", str(tmp_path / "state.json"), *extra])


def test_code_map_finds_classes_and_endpoints() -> None:
    assert rs.symbols("java", JAVA) == ["OrderController"]
    assert rs.endpoints(JAVA) == ["GET /api/orders/{id}"]  # bare @PostMapping has no path to show
    assert rs.endpoints(PY) == ["GET /health"]
    assert rs.symbols("python", PY) == ["Health", "health()"]


def test_sync_uploads_changes_only_and_removes_deleted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    up = FakeUploader()
    report = rs.sync(args_for(repo, tmp_path, "--history", "20"), up)
    assert report["failed"] == [] and report["uploaded"] == 5  # 2 docs, 2 module maps (server, tools), history
    titles = up.titles()
    assert "shop · docs › 설계.md" in titles and "shop · README.md" in titles
    assert not any("node_modules" in t for t in titles)
    code_map = next(payload.decode() for title, payload, _ in up.docs.values() if "코드지도 server" in title)
    assert "GET /api/orders/{id}" in code_map and "OrderController" in code_map
    file_line = next(line for line in code_map.splitlines() if "`OrderController.java`" in line)
    assert "`GET /api/orders/{id}`" in file_line  # the endpoint also sits next to its file
    history = next(payload.decode() for title, payload, _ in up.docs.values() if "최근 변경 이력" in title)
    assert "first" in history
    assert all("repo:shop" in tags for _, _, tags in up.docs.values())

    again = rs.sync(args_for(repo, tmp_path, "--history", "20"), up)
    assert again["uploaded"] == 0 and again["unchanged"] == 5

    write(repo, "docs/설계.md", "# 주문 설계\n\n주문은 결제 후 확정하고, 취소는 24시간 안에 한다.\n")
    (repo / "README.md").unlink()
    commit(repo, "second")
    third = rs.sync(args_for(repo, tmp_path, "--history", "20"), up)
    assert third["uploaded"] == 2  # the changed doc and the history
    assert third["deleted"] == 1   # README.md
    assert "shop · README.md" not in up.titles()
    assert any("취소는 24시간" in payload.decode() for title, payload, _ in up.docs.values() if title.endswith("설계.md"))
    assert len(up.deleted) == 3    # old 설계.md, old history, README.md


def test_code_mode_indexes_source_files_with_path_and_lines(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, "server/src/test/OrderControllerTest.java", "class OrderControllerTest {}\n")
    write(repo, "web/src/Cart.tsx", "\n".join(f"export const line{i} = {i};" for i in range(1, 171)) + "\n")
    commit(repo, "code")
    up = FakeUploader()
    report = rs.sync(args_for(repo, tmp_path, "--code", "--no-docs", "--no-code-map", "--history", "0"), up)
    assert report["failed"] == []
    titles = up.titles()
    assert "shop · server › src › OrderController.java.md" in titles
    assert "shop · web › src › Cart.tsx.md" in titles
    assert not any("Test.java" in t for t in titles)  # tests are skipped by default
    java = next(p.decode() for t, p, tags in up.docs.values() if "OrderController.java" in t)
    assert "> 엔드포인트: `GET /api/orders/{id}`" in java and "## server/src/OrderController.java L1-12" in java
    assert "```java" in java and "@RequestMapping(\"/api/orders\")" in java
    cart = next(p.decode() for t, p, _ in up.docs.values() if "Cart.tsx" in t)
    assert [line for line in cart.splitlines() if line.startswith("## ")] == [
        "## web/src/Cart.tsx L1-80", "## web/src/Cart.tsx L81-160", "## web/src/Cart.tsx L161-171"]
    assert all("kind:code" in tags for _, _, tags in up.docs.values())
    assert rs.sync(args_for(repo, tmp_path, "--code", "--no-docs", "--no-code-map", "--history", "0"), up)["uploaded"] == 0


def test_summaries_sit_on_every_block_and_are_reused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, "web/src/Cart.tsx", "\n".join(f"export const line{i} = {i};" for i in range(1, 171)) + "\n")
    commit(repo, "code")
    calls: list[str] = []

    def summarizer(path: str, text: str) -> str:
        calls.append(path)
        if path.endswith("app.py"):
            raise RuntimeError("model down")
        return f"{PurePosixPath(path).name} 요약"

    up = FakeUploader()
    args = args_for(repo, tmp_path, "--code", "--no-docs", "--no-code-map", "--history", "0")
    report = rs.sync(args, up, summarizer=summarizer)
    assert report["summarized"] == 2 and report["failed"] == []
    cart = next(p.decode() for t, p, _ in up.docs.values() if "Cart.tsx" in t)
    assert "> 요약: Cart.tsx 요약" in cart
    assert cart.count("> Cart.tsx 요약") == 3  # one per 80-line block
    py = next(p.decode() for t, p, _ in up.docs.values() if "app.py" in t)
    assert "요약" not in py  # summary failed: uploaded without it

    calls.clear()
    again = rs.sync(args, up, summarizer=summarizer)
    assert calls == ["tools/app.py"] and again["uploaded"] == 1  # only the file whose summary failed is retried

    plain = rs.sync(args_for(repo, tmp_path, "--code", "--no-docs", "--no-code-map", "--history", "0"), up)
    assert plain["uploaded"] == 2  # turning summaries off re-uploads the summarized files (app.py had none)


def test_summarizer_calls_an_openai_compatible_endpoint() -> None:
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>x</think>\n로그인 화면을\n구성합니다."}}]})

    summarize = rs.Summarizer("http://llm.test/v1/", "m1", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert summarize("a/sign-in.tsx", "code") == "로그인 화면을 구성합니다."
    assert seen["url"] == "http://llm.test/v1/chat/completions" and '"model":"m1"' in seen["body"]


def test_plain_folder_is_read_from_disk(tmp_path: Path) -> None:
    folder = tmp_path / "scripts"
    write(folder, "etl/load.py", "def load():\n    return 1\n")
    write(folder, "node_modules/lib/index.js", "export const x = 1;\n")
    write(folder, "notes.md", "# 메모\n적재는 매일 새벽 2시\n")
    up = FakeUploader()
    args = rs.parse_args(["--repo", str(folder), "--name", "scripts", "--code", "--state", str(tmp_path / "s.json")])
    report = rs.sync(args, up)
    assert report["failed"] == []
    assert up.titles() == ["scripts · etl › load.py.md", "scripts · notes.md", "scripts · 코드지도 etl.md"]
    assert any("ref:작업 폴더" in tags for _, _, tags in up.docs.values())
    write(folder, "etl/load.py", "def load():\n    return 2\n")
    again = rs.sync(args, up)
    # only the edited file: its names and line count did not change, so the code map stays as it was
    assert again["uploaded"] == 1 and again["unchanged"] == 2
    assert any("return 2" in p.decode() for t, p, _ in up.docs.values() if "load.py" in t)


def test_failed_upload_keeps_the_old_document(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    up = FakeUploader()
    rs.sync(args_for(repo, tmp_path, "--no-code-map", "--history", "0"), up)
    write(repo, "docs/설계.md", "# 바뀐 설계\n")
    commit(repo, "change")

    class Failing(FakeUploader):
        def upload(self, title, payload, tags):
            raise RuntimeError("api down")

    failing = Failing()
    failing.docs = dict(up.docs)
    report = rs.sync(args_for(repo, tmp_path, "--no-code-map", "--history", "0"), failing)
    assert report["failed"] and not failing.deleted  # nothing removed, retried next run
