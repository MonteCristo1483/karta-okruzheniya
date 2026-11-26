-- v2 industry: промышленные источники Москвы с классом опасности.
-- Координаты — центры объектов из OSM (проверены запросом Overpass), список
-- редактируется в config/industry_sources.json. Класс I..V — СанПиН
-- 2.2.1/2.1.1.1200-03 (ориентировочные СЗЗ 1000/500/300/100/50 м).
-- Таблица идемпотентна (CREATE IF NOT EXISTS), наполняется
-- import_industry_sources.py (UPSERT по id).

CREATE TABLE IF NOT EXISTS industry_sources (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    hazard_class TEXT NOT NULL CHECK (hazard_class IN ('I', 'II', 'III', 'IV', 'V')),
    lon          DOUBLE PRECISION NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    source       TEXT NOT NULL,
    geometry     GEOMETRY(POINT, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS industry_sources_geometry_idx
    ON industry_sources USING GIST (geometry);
