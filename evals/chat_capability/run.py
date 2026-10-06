"""Measure chat capability extraction against labeled cases with a real LLM.

Usage (ChatGPT OAuth, read-only copy of a Codex login; refresh stays off):
    uv run python evals/chat_capability/run.py --auth ~/.codex/auth.json --model gpt-5.6-luna

The role release is rebuilt from the approved product-role evidence so the allowed
capability list matches production.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import anyio

import jobtology_be.main  # noqa: F401  (loads the package in an order free of import cycles)
from jobtology_be.application.queries import CapabilityView, GoalView
from jobtology_be.chat.service import ChatCapabilityService
from jobtology_be.llm.client import ChatGptOAuthClient, LlmMessage, LlmUnavailableError
from jobtology_be.llm.credentials import FileCredentialStore
from jobtology_be.product_roles.builder import build_product_roles
from jobtology_be.product_roles.models import ArtifactApproval, ProductRoleInputs, ProductRolePolicy

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".omo/evidence/daily-data-roadmap/draft-review-historical/product-role-inputs.json"
USER = UUID(int=1)


class SingleGoalQueries:
    def __init__(self, occupation_id: str) -> None:
        self.occupation_id = occupation_id

    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]:
        now = datetime.now(UTC)
        return (GoalView(USER, "TARGETED", self.occupation_id, now, "UTC", "6개월", "ACTIVE", now, now),)

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]:
        return ()


def _snapshots():
    draft = build_product_roles(
        ProductRoleInputs.model_validate_json(EVIDENCE.read_text()),
        ProductRolePolicy.model_validate_json((ROOT / "config/product_roles/policy.v1.json").read_text()),
    )
    approval = ROOT / f"config/product_roles/approvals/{draft.digest}.json"
    return draft.publish(ArtifactApproval.model_validate_json(approval.read_text())).reader.snapshots


async def _run(auth: Path, model: str, cases_path: Path, concurrency: int) -> dict:
    snapshots = _snapshots()
    client = ChatGptOAuthClient(store=FileCredentialStore(auth), model=model)
    cases = json.loads(cases_path.read_text())["cases"]
    results: list[dict] = []
    limiter = anyio.Semaphore(concurrency)

    async def one(case: dict) -> None:
        async with limiter:
            service = ChatCapabilityService(
                llm=client, queries=SingleGoalQueries(case["occupation"]),
                snapshots=lambda: snapshots, display_name=lambda _: None,
            )
            messages: list[LlmMessage] = []
            for index, text in enumerate(case["messages"]):
                if index:
                    messages.append(LlmMessage("assistant", "더 자세히 말해줄래요?"))
                messages.append(LlmMessage("user", text))
            try:
                reply = await service.respond(USER, tuple(messages))
                picked = sorted(item.entity_id.rsplit(":", 1)[-1] for item in reply.candidates)
                error = None
            except LlmUnavailableError as exc:
                picked, error, reply = [], str(exc), None
            results.append({
                **case, "picked": picked, "error": error,
                "reply": reply.reply if reply else None,
                "quotes": [item.evidence_quote for item in reply.candidates] if reply else [],
            })

    async with anyio.create_task_group() as group:
        for case in cases:
            group.start_soon(one, case)
    return _score(sorted(results, key=lambda item: item["id"]))


def _score(results: list[dict]) -> dict:
    picks = correct = expected = found = negatives = negative_fp = 0
    mismatches: list[dict] = []
    for item in results:
        ok = set(item["expected"]) | set(item["acceptable"])
        wrong = [code for code in item["picked"] if code not in ok]
        missed = [code for code in item["expected"] if code not in item["picked"]]
        picks += len(item["picked"])
        correct += len(item["picked"]) - len(wrong)
        expected += len(item["expected"])
        found += len(item["expected"]) - len(missed)
        if not ok:
            negatives += 1
            negative_fp += bool(item["picked"])
        if wrong or missed or item["error"]:
            mismatches.append({k: item[k] for k in ("id", "messages", "expected", "picked", "quotes", "error")}
                              | {"wrong": wrong, "missed": missed})
    return {
        "cases": len(results),
        "errors": sum(bool(item["error"]) for item in results),
        "precision": round(correct / picks, 3) if picks else None,
        "recall": round(found / expected, 3) if expected else None,
        "negative_cases": negatives,
        "negative_false_positive_cases": negative_fp,
        "mismatches": mismatches,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.v1.json"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = anyio.run(_run, args.auth.expanduser(), args.model, args.cases, args.concurrency)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
