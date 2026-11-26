-- v2 industry: статус источника (проверка открытых источников 26.11.2026).
-- operating      — работает, штрафует по классу опасности;
-- conservation   — в консервации (МСЗ-2), вклад 0 без флага;
-- outside_moscow — вне Москвы (Москокс, Видное), вклад 0, из московской модели исключён;
-- unknown        — статус/выбросы не подтверждены (МЗП, технопарк), вклад 0 + флаг.
-- Идемпотентно: ADD COLUMN IF NOT EXISTS + пересоздание CHECK-констрейнта.

ALTER TABLE industry_sources
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'operating';

ALTER TABLE industry_sources
    DROP CONSTRAINT IF EXISTS industry_sources_status_check;

ALTER TABLE industry_sources
    ADD CONSTRAINT industry_sources_status_check
    CHECK (status IN ('operating', 'conservation', 'outside_moscow', 'unknown'));

CREATE INDEX IF NOT EXISTS industry_sources_status_idx
    ON industry_sources (status);
