from __future__ import annotations

import json
from datetime import date, datetime

import httpx
from fastapi.testclient import TestClient

from devcolleague.container import Container
from devcolleague.llm.types import ChatEvent
from devcolleague.services.daily_report import day_range, default_report_date

from .conftest import FakeProvider
from .test_api import parse_sse

DAY = date(2026, 11, 9)


def test_default_report_date() -> None:
    # 週二早上 10 點前：填寫週一
    assert default_report_date(datetime(2026, 11, 10, 9, 59)) == date(2026, 11, 9)
    # 週二 10 點後：填寫當天
    assert default_report_date(datetime(2026, 11, 10, 10, 0)) == date(2026, 11, 10)
    # 週一早上：往前跳過週末到週五
    assert default_report_date(datetime(2026, 11, 9, 8, 0)) == date(2026, 11, 6)
    # 週六下午：週五
    assert default_report_date(datetime(2026, 11, 7, 15, 0)) == date(2026, 11, 6)


def _in_day(hour: int) -> str:
    since, _ = day_range(DAY)
    return since.replace(hour=hour).isoformat()


def ado(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    params = req.url.params
    if path.endswith("/_apis/connectionData"):
        return httpx.Response(200, json={"authenticatedUser": {"id": "me", "providerDisplayName": "Aries"}})
    if path.endswith("/_apis/projects"):
        return httpx.Response(200, json={"value": [{"name": "THB"}, {"name": "Ocean"}]})
    if path.endswith("/_apis/git/pullrequests"):
        return httpx.Response(200, json={"value": []})
    if path.endswith("/_apis/git/repositories"):
        project = path.split("/")[-4]
        return httpx.Response(200, json={"value": [{"id": f"{project}-repo", "name": f"{project}-web"}]})
    if path.endswith("/commits"):
        project = path.split("/")[-6]
        message = "feat(樹木查詢): 新增條件查詢 API" if project == "THB" else "chore: 調整設定"
        return httpx.Response(200, json={"value": [
            {"commitId": f"sha-{project}", "comment": message, "author": {"name": "Aries", "date": _in_day(14)}},
        ]})
    if path.endswith("/_apis/wit/wiql"):
        return httpx.Response(200, json={"workItems": [{"id": 2}]})
    if path.endswith("/_apis/wit/workitems"):
        rel = "https://ads.example.com/_apis/wit/workItems/"
        items = {
            "1": {"id": 1, "fields": {"System.Title": "切版", "System.State": "Closed",
                                      "System.WorkItemType": "Task", "System.TeamProject": "THB"}},
            "2": {"id": 2, "fields": {"System.Title": "條件查詢 API", "System.State": "Active",
                                      "System.WorkItemType": "Task", "System.TeamProject": "THB"},
                  "relations": [{"rel": "System.LinkTypes.Hierarchy-Reverse", "url": rel + "100"}]},
            "100": {"id": 100, "fields": {"System.Title": "行道樹木查詢", "System.State": "Active",
                                          "System.WorkItemType": "User Story", "System.TeamProject": "THB"},
                    "relations": [{"rel": "System.LinkTypes.Hierarchy-Forward", "url": rel + "1"},
                                  {"rel": "System.LinkTypes.Hierarchy-Forward", "url": rel + "2"}]},
        }
        return httpx.Response(200, json={"value": [items[i] for i in params["ids"].split(",")]})
    return httpx.Response(404)


def _setup(client: TestClient, mock_handler) -> None:
    mock_handler["fn"] = ado
    client.post(
        "/api/connections",
        json={"kind": "ado", "name": "ADS", "base_url": "https://ads.example.com/Col", "token": "pat"},
    )
    resp = client.put(
        "/api/daily-report/settings",
        json={"projects": [{"name": "公路局", "source": "thb"}, {"name": "海洋", "source": ""}]},
    )
    assert resp.status_code == 200


def test_settings_and_sources(client: TestClient, mock_handler) -> None:
    _setup(client, mock_handler)
    settings = client.get("/api/daily-report/settings").json()
    assert settings["projects"] == [{"name": "公路局", "source": "thb"}, {"name": "海洋", "source": ""}]
    assert date.fromisoformat(settings["default_date"]).weekday() < 5

    assert client.get("/api/daily-report/sources").json() == {"sources": ["THB", "Ocean"], "errors": []}

    dup = client.put(
        "/api/daily-report/settings",
        json={"projects": [{"name": "公路局", "source": "a"}, {"name": "公路局", "source": "b"}]},
    )
    assert dup.status_code == 400


async def test_collect_groups_by_report_project(client: TestClient, container: Container, mock_handler) -> None:
    _setup(client, mock_handler)
    data = await container.daily_report.collect(DAY)

    assert data["errors"] == []
    assert data["project_names"] == ["公路局", "海洋", "未分類"]
    # 「海洋」沒有對應的平台專案，Ocean 專案的 commit 歸到「未分類」
    assert [p["name"] for p in data["projects"]] == ["公路局", "未分類"]

    thb = data["projects"][0]
    assert thb["progress"] == [{
        "id": "100", "title": "行道樹木查詢", "type": "User Story",
        "url": "https://ads.example.com/Col/THB/_workitems/edit/100", "percent": 50, "done": 1, "total": 2,
    }]
    assert thb["work_items_changed"][0]["parent"] == "行道樹木查詢"
    assert [c["message"] for c in thb["commits"]] == ["feat(樹木查詢): 新增條件查詢 API"]
    assert [c["message"] for c in data["projects"][1]["commits"]] == ["chore: 調整設定"]


def test_generate_discloses_data_and_uses_previous_report(
    client: TestClient, container: Container, mock_handler, monkeypatch
) -> None:
    _setup(client, mock_handler)
    client.post("/api/providers", json={"kind": "anthropic", "name": "Claude", "api_key": "sk-test"})
    fake = FakeProvider([[
        ChatEvent.text("### 公路局\n- 行道樹木查詢(50%)"),
        ChatEvent("done", {"stop_reason": "end_turn", "usage": {}}),
    ]])
    original_build = container.providers.build
    monkeypatch.setattr(container.providers, "build", lambda pid=None: (fake, original_build(pid)[1]))

    # 前一個工作日的定稿；空白內容的日報告不會被當成前一份
    saved = client.put("/api/daily-report/reports/2026-11-06", json={"content": "### 公路局\n- 行道樹木查詢(25%)"})
    assert saved.json()["date"] == "2026-11-06"
    client.put("/api/daily-report/reports/2026-11-07", json={"content": "", "notes": "草稿"})

    resp = client.post("/api/daily-report/generate", json={"day": DAY.isoformat(), "notes": " 海洋：內部進度會議 "})
    events = parse_sse(resp.text)
    assert [e[0] for e in events] == ["activity", "text_delta", "done"]
    disclosed = events[0][1]
    assert disclosed["notes"] == "海洋：內部進度會議"
    assert disclosed["previous_report"] == {"date": "2026-11-06", "content": "### 公路局\n- 行道樹木查詢(25%)"}

    # 揭露的資料（不含錯誤清單）與實際送給 LLM 的內容一致
    prompt = fake.requests[0].messages[0].content
    sent = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert sent == {k: v for k, v in disclosed.items() if k != "errors"}
    assert sent["projects"][0]["progress"][0]["percent"] == 50
    assert not fake.requests[0].tools


def test_generate_without_activity_or_notes_skips_llm(client: TestClient, container: Container, monkeypatch) -> None:
    client.post("/api/providers", json={"kind": "anthropic", "name": "Claude", "api_key": "sk-test"})
    fake = FakeProvider([])
    original_build = container.providers.build
    monkeypatch.setattr(container.providers, "build", lambda pid=None: (fake, original_build(pid)[1]))

    events = parse_sse(client.post("/api/daily-report/generate", json={"day": DAY.isoformat()}).text)
    # 以錯誤事件回報，前端不會把提示文字當成日報告內容儲存
    assert [e[0] for e in events] == ["activity", "error"]
    assert fake.requests == []


def test_report_save_and_load(client: TestClient) -> None:
    empty = client.get("/api/daily-report/reports/2026-11-09").json()
    assert empty == {"date": "2026-11-09", "content": "", "notes": "", "updated_at": None}

    client.put("/api/daily-report/reports/2026-11-09", json={"content": "v1", "notes": "n"})
    client.put("/api/daily-report/reports/2026-11-09", json={"content": "v2", "notes": "n"})
    loaded = client.get("/api/daily-report/reports/2026-11-09").json()
    assert (loaded["content"], loaded["notes"]) == ("v2", "n")
    assert loaded["updated_at"]

    assert client.get("/api/daily-report/reports/not-a-date").status_code == 422
