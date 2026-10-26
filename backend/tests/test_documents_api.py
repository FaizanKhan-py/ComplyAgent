import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app
from app.rag.embeddings import HashingEmbedder

from .conftest import AUTH, SAMPLES, upload
from .helpers import BrokenEmbedder, make_pdf


def chunk_rows(settings, doc_id=None):
    with sqlite3.connect(settings.db_path) as conn:
        if doc_id:
            return conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def test_upload_txt_is_extracted_chunked_embedded_and_indexed(client, settings):
    data = (SAMPLES / "hardship_assistance_policy.txt").read_bytes()
    response = upload(client, "hardship_assistance_policy.txt", data)
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "pending" and accepted["file_type"] == "txt"

    doc = client.get(f"/api/admin/documents/{accepted['id']}").json()
    assert doc["status"] == "indexed" and doc["error"] is None
    assert doc["chunk_count"] >= 2 and doc["char_count"] > 1000
    assert doc["page_count"] is None
    assert doc["embedding_model"] == HashingEmbedder().name
    assert doc["indexed_at"]
    assert chunk_rows(settings, doc["id"]) == doc["chunk_count"]
    assert (settings.uploads_dir / f"{doc['id']}.txt").read_bytes() == data


def test_upload_pdf_records_page_count(client):
    response = upload(client, "p.pdf", make_pdf(["Page one text here.", "Page two text here."]))
    doc = client.get(f"/api/admin/documents/{response.json()['id']}").json()
    assert doc["status"] == "indexed" and doc["page_count"] == 2 and doc["file_type"] == "pdf"


def test_invalid_files_are_rejected_with_clear_errors(client, settings):
    cases = [
        ("tool.exe", b"MZ\x90", 415, "unsupported_file_type"),
        ("notes.rtf", b"{\\rtf1 notes}", 415, "unsupported_file_type"),
        ("fake.docx", b"just some text", 400, "invalid_file"),
        ("fake.pdf", b"not a pdf at all", 400, "invalid_file"),
        ("empty.txt", b"", 400, "invalid_file"),
        ("binary.txt", b"\x00\x01\x02\x03", 400, "invalid_file"),
        ("big.txt", b"a" * (settings.max_upload_bytes + 1), 413, "file_too_large"),
    ]
    for name, data, status, code in cases:
        response = upload(client, name, data)
        assert response.status_code == status, name
        assert response.json()["error"]["code"] == code, name
    assert client.get("/api/admin/documents").json()["documents"] == []
    assert list(settings.uploads_dir.iterdir()) == []


def test_path_traversal_filename_is_stored_under_document_id(client, settings):
    response = upload(client, "../../../evil.txt", b"Harmless policy text about fees.")
    doc = response.json()
    assert doc["filename"] == "evil.txt"
    assert [p.name for p in settings.uploads_dir.iterdir()] == [f"{doc['id']}.txt"]
    assert not (settings.storage_dir.parent / "evil.txt").exists()


def test_duplicate_upload_is_rejected_and_ids_are_stable(client, settings):
    data = b"The same policy text, uploaded twice."
    first = upload(client, "a.txt", data)
    second = upload(client, "renamed.txt", data)
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_document"
    assert "a.txt" in second.json()["error"]["message"]
    assert len(client.get("/api/admin/documents").json()["documents"]) == 1

    # Deleting and re-uploading the same bytes gives the same document ID.
    doc_id = first.json()["id"]
    client.delete(f"/api/admin/documents/{doc_id}")
    assert upload(client, "a.txt", data).json()["id"] == doc_id


def test_listing_shows_status_and_metadata(client, indexed):
    documents = client.get("/api/admin/documents").json()["documents"]
    assert {d["filename"] for d in documents} == set(indexed)
    assert all(d["status"] == "indexed" and d["chunk_count"] > 0 for d in documents)
    assert {d["file_type"] for d in documents} == {"pdf", "docx", "txt", "md"}
    assert all("sha256" not in d and "stored_name" not in d for d in documents)


def test_extraction_failure_is_visible_and_leaves_no_chunks(client, settings):
    response = upload(client, "scanned.pdf", make_pdf(["", ""]))
    assert response.status_code == 202
    doc = client.get(f"/api/admin/documents/{response.json()['id']}").json()
    assert doc["status"] == "failed"
    assert "No extractable text" in doc["error"]
    assert doc["chunk_count"] == 0 and chunk_rows(settings) == 0
    events = client.get("/api/admin/activity").json()["events"]
    assert events[0]["event"] == "failed"
    assert client.get("/api/admin/kb/stats").json()["by_status"]["failed"] == 1


def test_embedding_failure_is_recorded_then_recoverable_by_reindex(settings, generator):
    broken = create_app(settings, embedder=BrokenEmbedder(), generator=generator)
    with TestClient(broken, headers=AUTH) as c:
        doc_id = upload(c, "policy.txt", b"Late fees are waived during a hardship plan.").json()[
            "id"
        ]
        doc = c.get(f"/api/admin/documents/{doc_id}").json()
        assert doc["status"] == "failed" and "Embedding provider is down" in doc["error"]
        assert chunk_rows(settings) == 0

    # The provider comes back: reindexing the stored file succeeds without a new upload.
    fixed = create_app(settings, embedder=HashingEmbedder(), generator=generator)
    with TestClient(fixed, headers=AUTH) as c:
        assert c.post(f"/api/admin/documents/{doc_id}/reindex").status_code == 202
        doc = c.get(f"/api/admin/documents/{doc_id}").json()
        assert doc["status"] == "indexed" and doc["error"] is None and doc["chunk_count"] == 1


def test_interrupted_ingestion_is_marked_failed_on_restart(settings, generator, client):
    doc_id = upload(client, "policy.txt", b"Some policy text.").json()["id"]
    client.app.state.kb.db.set_status(doc_id, "processing")  # simulate a crash mid-ingestion
    restarted = create_app(settings, embedder=HashingEmbedder(), generator=generator)
    with TestClient(restarted, headers=AUTH) as c:
        doc = c.get(f"/api/admin/documents/{doc_id}").json()
        assert doc["status"] == "failed" and "interrupted" in doc["error"]
        assert chunk_rows(settings) == 0


def test_delete_removes_file_chunks_and_search_results(client, settings, indexed):
    doc = indexed["hardship_assistance_policy.txt"]
    question = {"question": "How much can a hardship plan reduce the monthly installment?"}
    before = client.post("/api/admin/rag/query", json=question).json()
    assert any(s["document_id"] == doc["id"] for s in before["sources"])

    response = client.delete(f"/api/admin/documents/{doc['id']}")
    assert response.status_code == 200
    assert response.json() == {
        "id": doc["id"],
        "deleted": True,
        "chunks_removed": doc["chunk_count"],
    }
    assert chunk_rows(settings, doc["id"]) == 0
    assert not (settings.uploads_dir / f"{doc['id']}.txt").exists()
    assert client.get(f"/api/admin/documents/{doc['id']}").status_code == 404
    assert client.delete(f"/api/admin/documents/{doc['id']}").status_code == 404

    after = client.post("/api/admin/rag/query", json=question).json()
    assert all(s["document_id"] != doc["id"] for s in after["sources"])


def test_reindex_replaces_chunks_without_leaving_stale_ones(client, settings, indexed):
    doc = indexed["customer_contact_policy.md"]
    total = chunk_rows(settings)
    assert client.post(f"/api/admin/documents/{doc['id']}/reindex").status_code == 202
    again = client.get(f"/api/admin/documents/{doc['id']}").json()
    assert again["status"] == "indexed" and again["chunk_count"] == doc["chunk_count"]
    assert chunk_rows(settings) == total
    assert client.post("/api/admin/documents/missing/reindex").status_code == 404


def test_busy_document_cannot_be_deleted_or_reindexed(client, indexed):
    doc = indexed["customer_contact_policy.md"]
    client.app.state.kb.db.set_status(doc["id"], "processing")
    for response in (
        client.delete(f"/api/admin/documents/{doc['id']}"),
        client.post(f"/api/admin/documents/{doc['id']}/reindex"),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "document_busy"


def test_rebuild_reindexes_everything(client, settings, indexed):
    total = chunk_rows(settings)
    response = client.post("/api/admin/kb/rebuild")
    assert response.status_code == 202 and response.json() == {"queued": 4}
    stats = client.get("/api/admin/kb/stats").json()
    assert stats["status"] == "ready" and stats["total_chunks"] == total
