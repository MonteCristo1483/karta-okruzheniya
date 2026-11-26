#!/usr/bin/env python3
"""Скоринг v2 (docs/METHODOLOGY-V2.md): пять факторов.

Чистые функции без БД — их проверяют юнит-тесты. Все числа (веса, радиусы,
пороги, значения) берутся из config/scoring.json, блок `v2`.

Факторы (value 0..1):
  transport — давление близости транспорта (1 = дорога вплотную, penalty);
  access    — пешая доступность POI (1 = всё вплотную, bonus);
  green     — парк >= 0,5 га целиком в радиусе 300 м (1 = бонус);
  home      — под-индекс по году + серии + материалу (1 = новостройка);
  density   — давление плотности (1 = переуплотнение, penalty).

index = clamp(index.base + сумма sign * weight / scale * value, 0, 10).
Неизвестное -> value 0 и флаг not_classified. Никаких средних значений.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

def dec(value) -> Decimal:
    return Decimal(str(value))


def round_dec(value: Decimal, places: int = 3) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _cfg(cfg: dict) -> dict:
    return cfg["v2"]


def transport_quality_cfg(road_class: str | None, distance_m, cfg: dict) -> dict:
    """value = w_class * (1 - d/R)^exponent; d >= R -> 0."""
    transport = _cfg(cfg)["transport"]
    classes = transport["classes"]
    if road_class not in classes:
        return {"value": 0.0, "status": "not_classified", "class": road_class}
    spec = classes[road_class]
    radius = dec(spec["radius_m"])
    distance = dec(distance_m)
    if distance >= radius:
        return {
            "value": 0.0,
            "status": "outside",
            "class": road_class,
            "distance_m": float(round_dec(distance)),
            "radius_m": float(radius),
        }
    ratio = Decimal(1) - distance / radius
    value = dec(spec["weight"]) * (ratio ** dec(transport["exponent"]))
    return {
        "value": float(round_dec(value)),
        "status": "ok",
        "class": road_class,
        "distance_m": float(round_dec(distance)),
        "radius_m": float(radius),
    }


def best_transport_quality(per_class: dict[str, float], cfg: dict) -> dict:
    """max по классам; если классов нет — not_classified."""
    if not per_class:
        return {"value": 0.0, "status": "not_classified", "class": None}
    best = max(
        (transport_quality_cfg(name, dist, cfg) for name, dist in per_class.items()),
        key=lambda entry: entry["value"],
    )
    return best


def green_quality(parks: list[dict], cfg: dict) -> dict:
    """Парки: [{area_m2, distance_m, max_distance_m?}].

    distance_m — минимальное расстояние до полигона, max_distance_m — до самой
    дальней вершины (полигон целиком в радиусе, если max_distance_m <= R).
    Для старых данных без полигона max_distance_m нет -> сравниваем
    distance_m + эквивалентный радиус sqrt(area/pi) <= R.

    Полный бонус, если известная площадь >= min_area_ha и парк целиком внутри
    радиуса. Неизвестная площадь парка в радиусе -> not_classified (0).
    Нет парка в радиусе -> 0 (known_zero), ближайший дальше радиуса -> outside.
    """
    green = _cfg(cfg)["green"]
    radius = float(green["radius_m"])
    min_area = float(green["min_area_ha"]) * 10_000.0
    best = 0.0
    unknown_area = False
    nearest = None
    for park in parks:
        distance = float(park["distance_m"])
        if nearest is None or distance < nearest:
            nearest = distance
        if distance > radius:
            continue
        area = park.get("area_m2")
        if area is None:
            unknown_area = True
            continue
        if float(area) < min_area:
            continue
        if park.get("max_distance_m") is not None:
            inside = float(park["max_distance_m"]) <= radius
        else:
            equivalent_radius = math.sqrt(float(area) / math.pi)
            inside = distance + equivalent_radius <= radius
        if inside:
            best = 1.0
    if best > 0:
        status = "ok"
    elif unknown_area:
        status = "not_classified"
    else:
        status = "outside" if nearest is not None and nearest > radius else "known_zero"
    return {
        "value": best * float(green["value"]),
        "status": status,
        "distance_m": round(nearest, 1) if nearest is not None else None,
    }


def access_quality(distances: dict[str, float | None], cfg: dict) -> dict:
    """Доступность: сумма weight * max(0, 1 - d/R) по типам, веса в сумме 1.

    distances — {тип: расстояние до ближайшего POI | None, если слоя нет}.
    Если слоя типа нет в данных, его вес не участвует: веса нормируются на
    доступные типы, чтобы отсутствие данных не штрафовало всех. Такие типы
    попадают в `missing` и дают флаг access:<type>:not_classified.
    """
    access = _cfg(cfg)["access"]
    total_weight = 0.0
    total = 0.0
    per_type: dict[str, dict] = {}
    missing: list[str] = []
    for name, spec in access["types"].items():
        weight = float(spec["weight"])
        distance = distances.get(name)
        if distance is None:
            missing.append(name)
            continue
        radius = float(spec["radius_m"])
        component = max(0.0, min(1.0, 1.0 - float(distance) / radius))
        total += weight * component
        total_weight += weight
        per_type[name] = {
            "distance_m": round(float(distance), 1),
            "radius_m": radius,
            "component": round(component, 4),
            "weight": weight,
        }
    value = total / total_weight if total_weight > 0 else 0.0
    return {
        "value": round(value, 4),
        "status": "ok" if not missing else "not_classified",
        "types": per_type,
        "missing": missing,
    }


def _material_class(text: str | None, aliases: dict[str, list[str]]) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for canonical, markers in aliases.items():
        if any(marker in lowered for marker in markers):
            return canonical
    return None


def home_quality(year, series: str | None, material: str | None, cfg: dict) -> dict:
    """Под-индекс дома. Нет года -> 0 + not_classified."""
    home = _cfg(cfg)["home"]
    if year is None or int(year) <= 0:
        return {
            "value": float(home["value_if_year_missing"]),
            "status": "not_classified",
            "rule": "no_year",
            "year": None,
        }
    year = int(year)
    canonical = _material_class(material, home["materials"])
    if canonical is None:
        canonical = _material_class(series, home["series"])
    for rule in home["rules"]:
        if "min_year" in rule and year < int(rule["min_year"]):
            continue
        if "max_year" in rule and year > int(rule["max_year"]):
            continue
        if "materials" in rule and canonical not in rule["materials"]:
            continue
        return {
            "value": float(rule["value"]),
            "status": "ok",
            "rule": rule["id"],
            "year": year,
            "material": canonical,
        }
    return {
        "value": float(home["fallback_known"]),
        "status": "ok",
        "rule": "fallback_known",
        "year": year,
        "material": canonical,
    }


def density_pressure(density_m2_per_ha: float | None, cfg: dict) -> dict:
    """Давление плотности: 0 при <= comfort, 1 при >= critical, линейно между."""
    density = _cfg(cfg)["density"]
    if density_m2_per_ha is None:
        return {"value": 0.0, "status": "not_classified", "density_m2_per_ha": None}
    value = float(density_m2_per_ha)
    comfort = float(density["comfort_m2_per_ha"])
    critical = float(density["critical_m2_per_ha"])
    if value <= comfort:
        pressure = 0.0
    elif value >= critical:
        pressure = 1.0
    else:
        pressure = (value - comfort) / (critical - comfort)
    return {
        "value": round(pressure, 4),
        "status": "ok",
        "density_m2_per_ha": round(value, 1),
    }


def density_from_floor_area(floor_area_m2: float | None, radius_m: float) -> float | None:
    """Жилая площадь в м² / площадь круга в га."""
    if floor_area_m2 is None:
        return None
    circle_ha = math.pi * float(radius_m) ** 2 / 10_000.0
    if circle_ha <= 0:
        return None
    return float(floor_area_m2) / circle_ha


def v2_index(factor_values: dict[str, float], cfg: dict) -> Decimal:
    """index = clamp(base + сумма sign_i * weight_i/scale * value_i, min, max)."""
    v2 = _cfg(cfg)
    scale = dec(v2["scale"])
    base = dec(cfg["index"]["base"])
    total = base
    for name, weight in v2["weights"].items():
        sign = Decimal(-1) if v2["signs"][name] == "penalty" else Decimal(1)
        total += sign * dec(weight) / scale * dec(factor_values.get(name, 0.0))
    low = dec(cfg["index"]["min"])
    high = dec(cfg["index"]["max"])
    places = int(cfg["index"]["rounding_decimals"])
    return round_dec(max(low, min(total, high)), places)


def build_v2_block(
    transport: dict,
    access: dict,
    green: dict,
    home: dict,
    density: dict,
    cfg: dict,
) -> dict:
    """Собирает breakdown.v2 + список флагов not_classified."""
    v2 = _cfg(cfg)
    values = {
        "transport": transport["value"],
        "access": access["value"],
        "green": green["value"],
        "home": home["value"],
        "density": density["value"],
    }
    factors = {}
    flags: list[str] = []
    for name, entry in (
        ("transport", transport),
        ("access", access),
        ("green", green),
        ("home", home),
        ("density", density),
    ):
        weight = float(v2["weights"][name])
        sign = -1.0 if v2["signs"][name] == "penalty" else 1.0
        factors[name] = {
            "value": float(entry["value"]),
            "weight": weight,
            "sign": v2["signs"][name],
            "contribution": round(
                sign * weight * float(entry["value"]) / float(v2["scale"]), 3
            ),
            **{key: value for key, value in entry.items() if key != "value"},
        }
        if name == "access":
            for missing in entry.get("missing") or []:
                flags.append(f"access:{missing}:not_classified")
            if entry.get("status") == "not_classified":
                flags.append("access:not_classified")
        elif entry.get("status") == "not_classified":
            flags.append(f"{name}:not_classified")
    return {
        "index": float(v2_index(values, cfg)),
        "factors": factors,
        "flags": flags,
    }
