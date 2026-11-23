-- V2-S1: район как параметр.
--
-- Функции возвращают строки из основной таблицы, пересечённые с полигоном
-- района districts.osm_id. В отличие от вьюх *_presnya (003) они принимают
-- любой район и не требуют отдельной вьюхи на каждый.
--
-- Старые вьюхи buildings_presnya / poi_presnya / linear_presnya НЕ удаляем:
-- на них ещё опираются исторические скрипты до шага 2.
--
-- Дополнительно идемпотентно добавляет tags/is_home, если база создана
-- до V2-S1 (импорт также умеет это сам).

ALTER TABLE buildings ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS is_home BOOLEAN NOT NULL DEFAULT TRUE;

-- Вьюха из 003 раскрыла b.* на момент создания и не увидела новые колонки.
-- Обновляем определение (не удаляем): старые скрипты продолжают работать.
-- На чистой базе presnensky ещё нет — тогда вьюху создаст extract_presnensky.py.
DO $$
BEGIN
    IF to_regclass('public.presnensky') IS NOT NULL THEN
        EXECUTE $view$
            CREATE OR REPLACE VIEW buildings_presnya AS
            SELECT b.*
            FROM buildings b
            CROSS JOIN presnensky pr
            WHERE ST_Intersects(b.geometry, pr.geometry)
        $view$;
    END IF;
END
$$;

-- Функции создаём до появления districts/poi/linear в чистой базе; вызов
-- всё равно требует эти таблицы.
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION district_buildings(d_id BIGINT)
RETURNS SETOF buildings
LANGUAGE sql
STABLE
AS $$
    SELECT b.*
    FROM buildings b
    JOIN districts d ON d.osm_id = d_id
    WHERE ST_Intersects(b.geometry, d.geometry)
$$;

CREATE OR REPLACE FUNCTION district_poi(d_id BIGINT)
RETURNS SETOF poi
LANGUAGE sql
STABLE
AS $$
    SELECT p.*
    FROM poi p
    JOIN districts d ON d.osm_id = d_id
    WHERE ST_Intersects(p.geometry, d.geometry)
$$;

CREATE OR REPLACE FUNCTION district_linear(d_id BIGINT)
RETURNS SETOF linear
LANGUAGE sql
STABLE
AS $$
    SELECT l.*
    FROM linear l
    JOIN districts d ON d.osm_id = d_id
    WHERE ST_Intersects(l.geometry, d.geometry)
$$;
