-- T4: здания с вычисленной высотой в метрах (вьюха buildings_with_height).
--
-- height: числовой префикс тега height OSM ("12", "12.5", "12 м" -> 12/12.5);
-- если тега нет или он не парсится — фолбэк building:levels * 3.
-- levels приводится к числу и ограничивается диапазоном 1..60,
-- выбросы (вне диапазона) обнуляются (NULL).
-- height_source: 'tag' (из height), 'levels' (из этажности), NULL (высоты нет).

CREATE OR REPLACE VIEW buildings_with_height AS
WITH parsed AS (
    SELECT
        osm_id,
        geometry,
        height AS height_raw,
        building_levels AS levels_raw,
        replace(
            substring(height from '^\s*([0-9]+([.,][0-9]+)?)'),
            ',', '.'
        )::numeric AS height_tag,
        replace(
            substring(building_levels from '^\s*([0-9]+([.,][0-9]+)?)'),
            ',', '.'
        )::numeric AS levels_tag
    FROM buildings
)
SELECT
    osm_id,
    geometry,
    height_raw,
    levels_raw,
    CASE
        WHEN height_tag IS NOT NULL AND height_tag > 0 THEN height_tag
        WHEN levels_tag BETWEEN 1 AND 60 THEN levels_tag * 3
        ELSE NULL
    END AS height,
    CASE
        WHEN height_tag IS NOT NULL AND height_tag > 0 THEN 'tag'
        WHEN levels_tag BETWEEN 1 AND 60 THEN 'levels'
        ELSE NULL
    END AS height_source
FROM parsed;
