#!/usr/bin/env python3
"""Import existing answers without scheduling model calls; retain a raw backup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from reader_store import ReaderStore
from deploy_reader import load_paper_data


def load_anchor_map(store, paper, map_path):
    mapping = json.loads(Path(map_path).read_text(encoding="utf-8-sig"))
    if not isinstance(mapping, dict) or mapping.get("paper_id") != paper or not isinstance(mapping.get("anchors"), dict):
        raise ValueError("anchor map must contain matching paper_id and anchors:{annotation_id:anchor}")
    data = load_paper_data(store.paper_dir(paper) / "paper-data.js")
    version = data.get("content_version")
    if mapping.get("content_version") and mapping["content_version"] != version:
        raise ValueError("anchor map content_version does not match paper-data.js")
    views = {"body": data.get("sections", []), "translation": data.get("sections", [])}
    for name in ("overview", "explanation"):
        view = data.get(name, {})
        if isinstance(view, dict):
            views[name] = view.get("sections", [])
    ids = {name: {b.get("id") for s in sections for b in s.get("blocks", [])} for name, sections in views.items()}
    for aid, anchor in mapping["anchors"].items():
        if not isinstance(anchor, dict) or anchor.get("view") not in ids:
            raise ValueError("invalid anchor view for " + aid)
        if anchor.get("content_id") not in ids[anchor["view"]] and anchor.get("migration_status") != "unlocated":
            raise ValueError("anchor content_id not found in target view for " + aid)
        if anchor.get("content_version") and anchor["content_version"] != version:
            raise ValueError("anchor content_version mismatch for " + aid)
        if anchor.get("language") not in ("en", "zh"):
            raise ValueError("invalid anchor language for " + aid)
        if not isinstance(anchor.get("fragments", []), list):
            raise ValueError("anchor fragments must be an array for " + aid)
    return mapping


def apply_anchor_map(store, paper, map_path):
    """Repair only first-revision imported legacy anchors, never their Q/A fields."""
    mapping = load_anchor_map(store, paper, map_path)
    updated, skipped = 0, []
    with store.transaction():
        for aid, anchor in mapping["anchors"].items():
            row = store.db.execute("SELECT body,deleted FROM annotations WHERE paper_id=? AND id=?", (paper, aid)).fetchone()
            if not row:
                raise ValueError("anchor map refers to an unknown annotation: " + aid)
            anno = json.loads(row["body"])
            active = store.db.execute("SELECT 1 FROM tasks WHERE paper_id=? AND annotation_id=? AND state IN ('claimed','running','unknown')", (paper, aid)).fetchone()
            if row["deleted"] or not anno.get("legacy") or anno["revision"] != 1 or active:
                skipped.append(aid)
                continue
            if anno.get("anchor") == anchor:
                continue
            # Preserve id/revision/note/quote/answer/history/legacy/timestamps.
            anno["anchor"] = anchor
            store._save(anno)
            updated += 1
    store.project(paper)
    return {"anchors_updated": updated, "anchors_skipped": skipped}


def migrate(vault, paper, anchor_map=None):
    store = ReaderStore(vault)
    try:
        tasks_before = store.db.execute("SELECT COUNT(*) FROM tasks WHERE paper_id=?", (paper,)).fetchone()[0]
        path = store.paper_dir(paper) / "annotations.json"
        if path.exists():
            raw = path.read_bytes()
            destination = store.state_dir / "imports"
            destination.mkdir(exist_ok=True)
            backup = destination / (paper + "-" + hashlib.sha256(raw).hexdigest()[:16] + ".json")
            if not backup.exists():
                tmp = backup.with_suffix(".tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, backup)
        mapping = load_anchor_map(store, paper, anchor_map) if anchor_map else None
        count = store.import_legacy(paper, mapping["anchors"] if mapping else None)
        anchor_result = apply_anchor_map(store, paper, anchor_map) if anchor_map else {"anchors_updated": 0, "anchors_skipped": []}
        snapshot = store.list_annotations(paper)
        tasks_total = store.db.execute("SELECT COUNT(*) FROM tasks WHERE paper_id=?", (paper,)).fetchone()[0]
        return {"paper_id": paper, "imported": count, "total": len(snapshot["annotations"]),
                "answered": sum(a["status"] == "answered" for a in snapshot["annotations"]),
                "tasks_created": tasks_total - tasks_before, "tasks_total": tasks_total,
                **anchor_result}
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate annotations to reader v3 SQLite without replaying answers")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--anchor-map", help="Optional migration_anchor_map.json; repairs first-revision legacy anchors only")
    args = parser.parse_args()
    print(json.dumps(migrate(Path(args.vault).resolve(), args.paper, args.anchor_map), ensure_ascii=False, indent=2))
