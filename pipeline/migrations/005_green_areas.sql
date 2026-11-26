-- v2 green: полигоны зелени OSM с площадью в м2 (геодезической).
-- Нужны для фактора «зелень»: парк >= 0,5 га, целиком попадающий в 300 м.
-- Таблица идемпотентна (CREATE IF NOT EXISTS), наполняется import_v2_layers.py.

CREATE TABLE IF NOT EXISTS green_areas (
    osm_type TEXT NOT NULL,
    osm_id   BIGINT NOT NULL,
    kind     TEXT NOT NULL,
    area_m2  DOUBLE PRECISION NOT NULL,
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
);

CREATE INDEX IF NOT EXISTS green_areas_geometry_idx
    ON green_areas USING GIST (geometry);

CREATE INDEX IF NOT EXISTS green_areas_area_idx
    ON green_areas (area_m2);
