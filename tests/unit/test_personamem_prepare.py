import csv
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from benchmark.aml.eval import prepare_personamem
from benchmark.aml.eval.run_public import run_public


def test_multimodal_or_unknown_messages_fail_in_text_mode():
    for message in [
        {"role": "user", "content": [{"type": "image"}]},
        {"role": "tool", "content": "text"},
    ]:
        with pytest.raises(ValueError, match="Unsupported text message at source index 0"):
            prepare_personamem.chat_messages([message])


async def test_csv_links_labels_and_user_isolation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    output = tmp_path / "prepared"
    messages = [
        {"role": "user", "content": "  Original text\n"},
        {"role": "assistant", "content": "reply"},
    ]
    row = {
        "persona_id": "7",
        "raw_persona_file": "data/raw_data/persona7.json",
        "correct_answer": "GOLD ONLY",
        "incorrect_answers": repr(["WRONG ONE", "WRONG TWO", "WRONG THREE"]),
        "preference": "ANNOTATION ONLY",
    }
    histories = []
    for size in ("32k", "128k"):
        relative = f"data/chat_history_{size}/exact7.json"
        row[f"chat_history_{size}_link"] = relative
        history = source / relative
        history.parent.mkdir(parents=True)
        history.write_text(
            json.dumps(
                {
                    "metadata": {"persona_id": 7, "input_filename": row["raw_persona_file"]},
                    "chat_history": [
                        {"role": "system", "content": f"EXPLICIT {size} PERSONA"},
                        messages[0],
                        {"role": "system", "content": "ANOTHER PROFILE"},
                        messages[1],
                        {"role": "assistant", "content": " \n"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        history.with_name("newer7.json").write_text("invalid alternative", encoding="utf-8")
        histories.append(history)

    questions = ["First question?", "Second question?"]
    csv_file = source / "benchmark/text/benchmark.csv"
    csv_file.parent.mkdir(parents=True)
    with csv_file.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*row, "user_query"])
        writer.writeheader()
        for question in questions:
            writer.writerow({**row, "user_query": repr({"role": "user", "content": question})})
    verification = tmp_path / "verification.json"
    verification.write_text(
        json.dumps(
            {
                "success": True,
                "revision": "fixture-revision",
                "files": [
                    {
                        "path": path.relative_to(source).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in [csv_file, *histories]
                ],
            }
        ),
        encoding="utf-8",
    )

    case_id = "personamem-v2-row-0-persona-7"
    unit32 = "personamem_v2_32k"
    unrelated = {"unit": "clbench_0_4k", "case_id": "unrelated", "histories": []}
    smoke = tmp_path / "smoke.json"
    smoke.write_text(
        json.dumps(
            {
                "cases": [unrelated, {"unit": unit32, "case_id": case_id}],
                "datasets": [{"unit": unit32}, {"unit": unrelated["unit"]}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_personamem",
            "--source-dir",
            str(source),
            "--verification",
            str(verification),
            "--output-dir",
            str(output),
            "--smoke-file",
            str(smoke),
        ],
    )
    prepare_personamem.main()

    units = [unit32, "personamem_v2_128k"]
    prepared = [output / f"{unit}.json" for unit in units]
    for path, history in zip(prepared, histories, strict=True):
        payload = json.loads(path.read_bytes())
        (shared,) = payload["histories"]
        assert shared["sessions"] == [{"session_id": "persona-7", "messages": messages}]
        first = payload["cases"][0]["provenance"]
        assert first["source_relative_path"] == history.relative_to(source).as_posix()
        assert first["dropped_system_message_indices"] == [0, 2]
        assert first["dropped_blank_message_indices"] == [4]
    refreshed = json.loads(smoke.read_bytes())
    unchanged, updated = refreshed["cases"]
    assert unchanged == unrelated
    assert updated["histories"] == [{"session_id": "persona-7", "messages": messages}]

    requests = []

    def handle(request):
        data = json.loads(request.content)
        requests.append((request.url.path, data))
        if request.url.path == "/add":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"data": [{"id": "memory", "content": data["user_id"]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        summary = await run_public(
            prepared,
            output_dir=tmp_path / "retrieval",
            namespace="same-run",
            base_url="http://aml.test",
            api_key="test-only",
            concurrency=1,
            client=client,
        )

    assert [path for path, _ in requests] == ["/add", "/search", "/search"] * 2
    assert [unit["unit"] for unit in summary["units"]] == units
    assert requests[0][1]["user_id"] != requests[3][1]["user_id"]
    for offset, unit in zip((0, 3), summary["units"], strict=True):
        addition = requests[offset][1]
        assert addition["messages"] == [
            {"role": "user", "content": "Original text"},
            {"role": "assistant", "content": "reply"},
        ]
        assert [body for _, body in requests[offset + 1 : offset + 3]] == [
            {"query": question, "user_id": addition["user_id"], "top_k": 100}
            for question in questions
        ]
        rows = [
            json.loads(line)
            for line in Path(unit["retrieval"]).read_text(encoding="utf-8").splitlines()
        ]
        assert [item["question"] for item in rows] == questions
        for item in rows:
            assert item["retrieved_context"] == [addition["user_id"]]
            assert item["gold_answer"] == row["correct_answer"]
            assert item["incorrect_answers"] == ["WRONG ONE", "WRONG TWO", "WRONG THREE"]
            assert item["preference"] == row["preference"]
            assert item["answer_mode"] == "mcq"
            assert item["options"] == []

    previous_outputs = {path: path.read_bytes() for path in [*prepared, smoke]}
    histories[1].write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Source differs from verified download.*128k"):
        prepare_personamem.main()
    assert {path: path.read_bytes() for path in previous_outputs} == previous_outputs
