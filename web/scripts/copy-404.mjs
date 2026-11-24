// Next (app router) сам пишет dist/404.html — это страница _not-found с
// чанками приложения — и затирает public/404.html. Для старых ссылок
// «Поделиться» /r/<id>/ нужен автономный редирект, который работает даже
// без JS-бандла, поэтому после сборки возвращаем public/404.html наверх.
import { copyFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
copyFileSync(
  join(webRoot, "public", "404.html"),
  join(webRoot, "dist", "404.html"),
);
console.log("dist/404.html ← public/404.html");
