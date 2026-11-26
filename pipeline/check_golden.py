#!/usr/bin/env python3
"""Проверка golden set по выгрузке (Эталон-2, шаг 2).

Читает `data/golden/facts.json` и `presnya_buildings.geojson`, проверяет:
  * компонент вообще есть в разбивке;
  * знак совпадает с фактом (бонус > 0 / штраф < 0 / ровно 0);
  * расстояние в допуске (если факт это требует).

Работает без PostGIS — поэтому запускается в Deploy после валидатора и
до публикации. Несовпадение знака — критично (exit 1), расстояние —
предупреждение (данные могли обновиться).

  python pipeline/check_golden.py web/public/data
  python pipeline/check_golden.py web/public/data --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FACTS = ROOT / "data" / "golden" / "facts.json"
EPS = 0.001


def load_facts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("facts") or []


def load_buildings(data_dir: Path) -> dict[int, dict]:
    path = data_dir / "presnya_buildings.geojson"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        feature["properties"]["id"]: feature["properties"]
        for feature in payload.get("features") or []
    }


def component(breakdown: dict, kind: str, factor: str) -> dict | None:
    if kind == "factor":
        return ((breakdown.get("v2") or {}).get("factors") or {}).get(factor)
    bucket = "bonuses" if kind == "bonus" else "penalties"
    return (breakdown.get(bucket) or {}).get(factor)


def flags(breakdown: dict) -> list[str]:
    return list((breakdown.get("v2") or {}).get("flags") or [])


def check(facts: list[dict], buildings: dict[int, dict]) -> dict:
    results = []
    for fact in facts:
        building_id = fact["building_id"]
        properties = buildings.get(building_id)
        if properties is None:
            results.append({"fact": fact, "status": "critical",
                            "reason": "нет дома в выгрузке"})
            continue
        breakdown = properties.get("breakdown") or {}
        entry = component(breakdown, fact["type"], fact["factor"])
        if entry is None:
            results.append({"fact": fact, "status": "critical",
                            "reason": f"нет компонента {fact['factor']}"})
            continue
        if fact["type"] == "factor":
            value = entry.get("value")
        else:
            value = entry.get("bonus") if fact["type"] == "bonus" else entry.get("penalty")
        if value is None:
            results.append({"fact": fact, "status": "critical",
                            "reason": "компонент без значения"})
            continue

        required_flag = fact.get("flag")
        if required_flag and required_flag not in flags(breakdown):
            results.append({
                "fact": fact, "status": "critical",
                "reason": f"нет флага {required_flag}",
            })
            continue

        expected = fact["expect"]
        if expected == "positive":
            if fact["type"] == "factor":
                ok = value > EPS
            else:
                ok = value > EPS if fact["type"] == "bonus" else value < -EPS
        else:
            ok = abs(value) <= EPS
        if not ok:
            results.append({
                "fact": fact, "status": "critical",
                "reason": f"ожидали {expected}, получили {value}",
            })
            continue

        if fact.get("check_distance") and entry.get("distance_m") is not None:
            delta = abs(float(entry["distance_m"]) - float(fact["distance_m"]))
            if delta > float(fact.get("tolerance_m") or 0):
                results.append({
                    "fact": fact, "status": "warning",
                    "reason": f"расстояние {entry['distance_m']} м vs факт "
                              f"{fact['distance_m']} м (Δ {delta:.1f})",
                })
                continue

        results.append({"fact": fact, "status": "ok", "reason": ""})
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "critical": sum(1 for r in results if r["status"] == "critical"),
        "warnings": sum(1 for r in results if r["status"] == "warning"),
        "results": results,
    }


def format_report(report: dict) -> str:
    lines = [
        f"Golden set: {report['ok']}/{report['total']} ок, "
        f"критичных {report['critical']}, предупреждений {report['warnings']}"
    ]
    for item in report["results"]:
        if item["status"] == "ok":
            continue
        fact = item["fact"]
        where = fact.get("address") or fact["building_id"]
        lines.append(
            f"  [{item['status']}] id {fact['building_id']} «{where}» "
            f"{fact['factor']} ({fact['source']}): {item['reason']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка golden set")
    parser.add_argument("data_dir", nargs="?", default=str(ROOT / "web" / "public" / "data"))
    parser.add_argument("--facts", default=str(DEFAULT_FACTS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    facts = load_facts(Path(args.facts))
    buildings = load_buildings(Path(args.data_dir))
    report = check(facts, buildings)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 1 if report["critical"] else 0


if __name__ == "__main__":
    sys.exit(main())
