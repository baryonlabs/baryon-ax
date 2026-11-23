from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
