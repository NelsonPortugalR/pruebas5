#!/usr/bin/env python3
"""Revisa los datos estructurados (JSON-LD) del sitio contra lo que Google exige y recomienda para los tipos
que usa Search Console: videos, fichas de comercio y fragmentos de producto, eventos y recetas.

    python3 validar_sd.py --dist /ruta/a/dist          # sobre los archivos generados
    python3 validar_sd.py --vivo https://ollasarequipa.com   # sobre la web publicada (lee el sitemap)

Sale con código 1 si encuentra un problema crítico (lo que impide el resultado enriquecido o contradice las pautas).
Lo opcional que falta se lista aparte: solo se completa con datos reales (reseñas, calorías, política de devoluciones...)."""
import argparse
import collections
import json
import os
import re
import sys
import urllib.request

AGENTE = "Mozilla/5.0 (compatible; comprobacion-del-sitio)"
FECHA_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DURACION = re.compile(r"^PT(\d+H)?(\d+M)?(\d+(\.\d+)?S)?$")


def bajar(url, binario=False):
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=40) as r:
        datos = r.read()
        return (r.status, r.headers.get("content-type", ""), datos if binario else datos.decode("utf-8", "replace"))


def bloques(html):
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            yield json.loads(m.group(1))
        except ValueError as e:
            yield {"@type": "JSON ROTO", "error": str(e)}


def nodos(o, camino=""):
    if isinstance(o, dict):
        if "@graph" in o:
            for x in o["@graph"]:
                yield from nodos(x, camino)
            return
        if "@type" in o:
            yield camino, o
        for k, v in o.items():
            if not k.startswith("@"):
                yield from nodos(v, camino + "/" + k)
    elif isinstance(o, list):
        for x in o:
            yield from nodos(x, camino)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist")
    ap.add_argument("--vivo")
    ap.add_argument("--dominio", default="https://ollasarequipa.com")
    args = ap.parse_args()

    paginas = {}   # ruta -> html
    if args.dist:
        for base, _, fs in os.walk(args.dist):
            for f in sorted(fs):
                if f.endswith(".html"):
                    p = os.path.join(base, f)
                    rel = os.path.relpath(p, args.dist)
                    paginas["/" + re.sub(r"(^|/)index\.html$", r"\1", re.sub(r"\.html$", "", rel)) if rel != "index.html" else "/"] = open(p, encoding="utf-8").read()
    else:
        _, _, sm = bajar(args.vivo + "/sitemap.xml")
        for u in re.findall(r"<loc>([^<]+)</loc>", sm):
            st, _, html = bajar(u)
            paginas[u.replace(args.vivo, "") or "/"] = html

    criticos, opcionales = [], collections.Counter()
    cuenta = collections.Counter()
    videos = collections.defaultdict(list)     # contentUrl -> [(página, nombre, miniatura)]
    miniaturas = collections.defaultdict(set)  # miniatura -> {contentUrl}
    urls_a_comprobar = set()

    def falta(n, campos):
        return [c for c in campos if n.get(c) in (None, "", [], {})]

    for ruta, html in sorted(paginas.items()):
        es_ficha = ruta.startswith("/productos/")
        for b in bloques(html):
            for camino, n in nodos(b):
                t = n["@type"] if isinstance(n["@type"], str) else "+".join(n["@type"])
                cuenta[t] += 1
                if t == "JSON ROTO":
                    criticos.append("%s: JSON-LD que no se puede leer (%s)" % (ruta, n.get("error")))
                elif t == "VideoObject":
                    for c in falta(n, ["name", "thumbnailUrl", "uploadDate"]):
                        criticos.append("%s: video sin %s" % (ruta, c))
                    if n.get("uploadDate") and not FECHA_HORA.match(str(n["uploadDate"])):
                        criticos.append("%s: uploadDate sin hora o sin zona horaria (%s)" % (ruta, n["uploadDate"]))
                    if not (n.get("contentUrl") or n.get("embedUrl")):
                        criticos.append("%s: video sin contentUrl ni embedUrl" % ruta)
                    if "og-image" in str(n.get("thumbnailUrl")):
                        criticos.append("%s: la miniatura del video es la imagen general del sitio" % ruta)
                    if n.get("duration") and not DURACION.match(str(n["duration"])):
                        criticos.append("%s: duración mal escrita (%s)" % (ruta, n["duration"]))
                    for c in falta(n, ["description", "duration"]):
                        opcionales["video sin %s" % c] += 1
                    videos[n.get("contentUrl")].append((ruta, n.get("name"), n.get("thumbnailUrl")))
                    miniaturas[n.get("thumbnailUrl")].add(n.get("contentUrl"))
                    urls_a_comprobar.add(n.get("thumbnailUrl"))
                elif t == "Product":
                    if not es_ficha or camino:
                        criticos.append("%s: Product fuera de una ficha de producto (%s)" % (ruta, n.get("name")))
                        continue
                    for c in falta(n, ["name", "image"]):
                        criticos.append("%s: producto sin %s" % (ruta, c))
                    of = n.get("offers")
                    if not isinstance(of, dict):
                        criticos.append("%s: producto sin oferta, reseña ni valoración" % ruta)
                    else:
                        if not re.match(r"^\d+(\.\d{1,2})?$", str(of.get("price", ""))) or float(of.get("price") or 0) <= 0:
                            criticos.append("%s: precio mal escrito (%r)" % (ruta, of.get("price")))
                        if not re.match(r"^[A-Z]{3}$", str(of.get("priceCurrency", ""))):
                            criticos.append("%s: moneda mal escrita (%r)" % (ruta, of.get("priceCurrency")))
                        if of.get("@type") != "Offer":
                            criticos.append("%s: la oferta debe ser Offer" % ruta)
                        if of.get("availability") and not str(of["availability"]).startswith("https://schema.org/"):
                            criticos.append("%s: disponibilidad mal escrita" % ruta)
                        for c in falta(of, ["availability", "itemCondition", "url"]):
                            opcionales["oferta sin %s" % c] += 1
                        for c in falta(of, ["hasMerchantReturnPolicy", "shippingDetails", "priceValidUntil"]):
                            opcionales["oferta sin %s" % c] += 1
                    for c in falta(n, ["description", "brand", "sku"]):
                        opcionales["producto sin %s" % c] += 1
                    for c in falta(n, ["review", "aggregateRating"]):
                        opcionales["producto sin %s" % c] += 1
                    urls_a_comprobar.add(n.get("image") if isinstance(n.get("image"), str) else None)
                elif t == "Event":
                    criticos.append("%s: hay un Event declarado (%s); la agenda del equipo no debe ir como evento" % (ruta, n.get("name")))
                elif t == "Recipe":
                    for c in falta(n, ["name", "image"]):
                        criticos.append("%s: receta sin %s" % (ruta, c))
                    if n.get("totalTime") and not DURACION.match(str(n["totalTime"])):
                        criticos.append("%s: totalTime mal escrito" % ruta)
                    if n.get("datePublished") and not (FECHA.match(str(n["datePublished"])) or FECHA_HORA.match(str(n["datePublished"]))):
                        criticos.append("%s: datePublished mal escrito" % ruta)
                    ins = n.get("recipeInstructions") or []
                    if not ins or not all(isinstance(x, dict) and x.get("@type") == "HowToStep" and x.get("text") for x in ins):
                        criticos.append("%s: pasos de la receta mal formados" % ruta)
                    for c in falta(n, ["author", "datePublished", "description", "keywords", "recipeCategory", "recipeCuisine", "recipeIngredient", "recipeYield", "totalTime"]):
                        opcionales["receta sin %s" % c] += 1
                    for c in falta(n, ["aggregateRating", "video", "nutrition", "prepTime", "cookTime"]):
                        opcionales["receta sin %s" % c] += 1
                    if "og-image" in str(n.get("image")):
                        opcionales["receta con la imagen general del sitio (falta la foto del plato)"] += 1
                    urls_a_comprobar.add(n.get("image") if isinstance(n.get("image"), str) else None)

    for cu, usos in videos.items():
        if len(usos) > 1:
            criticos.append("el mismo video está declarado en %d páginas: %s" % (len(usos), ", ".join(u[0] for u in usos)))
    for mini, cus in miniaturas.items():
        if len(cus) > 1:
            criticos.append("la miniatura %s se usa para %d videos distintos" % (mini, len(cus)))

    # las imágenes declaradas existen
    comprobadas = 0
    for u in sorted(x for x in urls_a_comprobar if x):
        if not u.startswith(args.dominio):
            continue
        ruta = u[len(args.dominio):]
        if args.dist:
            if not os.path.exists(os.path.join(args.dist, ruta.lstrip("/"))):
                criticos.append("imagen declarada que no existe: %s" % ruta)
        else:
            try:
                st, ct, datos = bajar(u, binario=True)
                if st != 200 or not ct.startswith("image/") or len(datos) < 2000:
                    criticos.append("imagen declarada con problema: %s (%s, %s, %d bytes)" % (ruta, st, ct, len(datos)))
            except Exception as e:  # noqa: BLE001
                criticos.append("imagen declarada que no responde: %s (%s)" % (ruta, e))
        comprobadas += 1

    print("páginas revisadas: %d" % len(paginas))
    print("tipos: " + ", ".join("%s %d" % (k, v) for k, v in sorted(cuenta.items()) if k in ("VideoObject", "Product", "Event", "Recipe", "ItemList", "LocalBusiness", "FAQPage", "HowTo", "Article", "BreadcrumbList", "Service")))
    print("videos declarados: %d (con duración: %d) | imágenes declaradas comprobadas: %d" % (len(videos), sum(1 for p, h in paginas.items() for b in bloques(h) for _, n in nodos(b) if n.get("@type") == "VideoObject" and n.get("duration")), comprobadas))
    print("\nCRÍTICOS: %d" % len(criticos))
    for c in criticos[:60]:
        print("  - " + c)
    print("\nOPCIONALES QUE FALTAN (no impiden nada; solo se llenan con datos reales):")
    for k, v in sorted(opcionales.items()):
        print("  - %s: %d" % (k, v))
    return 1 if criticos else 0


if __name__ == "__main__":
    sys.exit(main())
