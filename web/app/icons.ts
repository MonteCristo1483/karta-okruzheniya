// SVG-иконки категорий: path d в viewBox 0 0 24 24 (stroke-стиль).
// Используются и в панели слоёв (React), и в пинах POI на карте (DOM).
export const circlePath = (cx: number, cy: number, r: number): string =>
  `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${r * 2} 0a${r} ${r} 0 1 0 ${-r * 2} 0`;

export const ICON_PATHS = {
  school:
    "M12 5 3.5 9.5 12 14l8.5-4.5L12 5zM6.5 11.5v3.8c0 1.4 2.5 2.7 5.5 2.7s5.5-1.3 5.5-2.7v-3.8",
  kindergarten: `${circlePath(7.5, 6.5, 2)}${circlePath(16.5, 6.5, 2)}${circlePath(
    12,
    13.5,
    5.5,
  )}M10 13h.1M14 13h.1M10.5 15.8c.9.8 2.1.8 3 0`,
  park: "M12 3.5 7 10.5h2.5L6 15.5h12l-3.5-5H17L12 3.5zM11 15.5h2v4.5h-2z",
  subway_entrance: `${circlePath(12, 12, 8.5)}M8 15.5v-7l4 4.2 4-4.2v7`,
  shop: "M6.5 8.5h11l-1 11h-9l-1-11zM9.5 8.5a2.5 2.5 0 0 1 5 0",
  redzones: "M12 4 21 19.5H3L12 4zM12 10v4M12 16.8v.2",
  routes: "M4.5 18.5c3.5-8 11.5-8 15-2.5M16.5 13.5l3 2.5-3.5 1.5",
} as const;

export type IconKey = keyof typeof ICON_PATHS;
