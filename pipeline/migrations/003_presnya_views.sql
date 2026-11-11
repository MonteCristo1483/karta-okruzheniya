-- T7: вьюхи с данными по району Пресненский (пересечение с полигоном
-- из таблицы presnensky, которую наполняет pipeline/extract_presnensky.py).

CREATE OR REPLACE VIEW buildings_presnya AS
SELECT b.*
FROM buildings b
CROSS JOIN presnensky pr
WHERE ST_Intersects(b.geometry, pr.geometry);

CREATE OR REPLACE VIEW poi_presnya AS
SELECT p.*
FROM poi p
CROSS JOIN presnensky pr
WHERE ST_Intersects(p.geometry, pr.geometry);

CREATE OR REPLACE VIEW linear_presnya AS
SELECT l.*
FROM linear l
CROSS JOIN presnensky pr
WHERE ST_Intersects(l.geometry, pr.geometry);
