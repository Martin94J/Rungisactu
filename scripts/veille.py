#!/usr/bin/env python3
"""Collecte (Bing/Google News + recherche web Claude), filtrage par Claude, images, export JSON."""
import base64, hashlib, json, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import anthropic, feedparser, requests, yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT, SEEN = ROOT / "data/articles.json", ROOT / "data/seen.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; VeilleBot/1.0)"}
NOW = datetime.now(timezone.utc)
CATS = "Politique locale, Urbanisme et projets, Économie et emploi, Transport et mobilité, Environnement, Sécurité et justice, Marchés publics, Événements, Autre"

def log(*a): print(*a, flush=True)
def norm(u): return re.sub(r"[?#].*$", "", u).rstrip("/").lower()
def uid(u): return hashlib.sha1(norm(u).encode()).hexdigest()[:16]
def clean(t): return re.sub(r"\s[-|–]\s[^-|–]+$", "", t)
def tkey(t): return re.sub(r"[^a-z0-9]", "", clean(t).lower())[:70]

def to_dt(x):
    try:
        d = datetime.fromisoformat(x) if isinstance(x, str) else datetime(*x[:6])
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def real_url(link):
    q = parse_qs(urlparse(link).query)
    if "url" in q: return q["url"][0]
    if "news.google.com" in link:  # anciens liens Google News : URL parfois encodée
        try:
            raw = base64.urlsafe_b64decode(link.split("/")[-1].split("?")[0] + "==")
            m = re.search(rb"https?://[^\x00-\x20\"<>]+", raw)
            if m: return m.group(0).decode()
        except Exception: pass
    return link

def rss(url):
    out = []
    for e in feedparser.parse(url, agent=UA["User-Agent"]).entries:
        src = (e.get("source") or {}).get("title") or e.get("news_source") or ""
        out.append(dict(title=e.get("title", ""), url=real_url(e.get("link", "")), source=src,
                        published=to_dt(e.get("published_parsed")) or NOW,
                        snippet=BeautifulSoup(e.get("summary", ""), "html.parser").get_text()[:300],
                        image=e.get("news_image")))
    return out

def collect_rss(t, days):
    res = []
    for q in t["queries"]:
        for u in (f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss&setmkt=fr-FR&qft=interval%3d%228%22",
                  f"https://news.google.com/rss/search?q={quote_plus(q + f' when:{days}d')}&hl=fr&gl=FR&ceid=FR:fr"):
            try: res += rss(u)
            except Exception as ex: log("  rss KO", ex)
    return res

def jlist(text):
    m = re.search(r"\[.*\]", text, re.S)
    try: return json.loads(m.group(0)) if m else []
    except Exception: return []

def discover(cl, t, S, days):
    prompt = (f"Veille de collectivité. Sujet : {t['label']}. Pertinent : {t['brief']}\n"
              "Cherche sur le web (presse locale et nationale, sites officiels, communiqués, appels d'offres, "
              f"publications publiques de LinkedIn, Facebook, X, Instagram si indexées) ce qui a été publié ces {days} derniers jours. "
              f"Requêtes de départ : {'; '.join(t['queries'])}.\n"
              "Réponds uniquement par un tableau JSON d'objets {\"title\",\"url\",\"source\",\"date\"(AAAA-MM-JJ ou null),"
              "\"snippet\",\"image\"(URL ou null)}. N'utilise que des URL réellement vues dans les résultats. [] si rien.")
    msgs = [{"role": "user", "content": prompt}]
    for _ in range(4):
        r = cl.messages.create(model=S["model_search"], max_tokens=4000, messages=msgs,
                               tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": S["discovery_max_searches"]}])
        if r.stop_reason != "pause_turn": break
        msgs = [msgs[0], {"role": "assistant", "content": r.content}]
    items = jlist("".join(b.text for b in r.content if b.type == "text"))
    return [dict(title=i.get("title", ""), url=i["url"], source=i.get("source") or urlparse(i["url"]).netloc,
                 published=to_dt(i.get("date") or "") or NOW, snippet=(i.get("snippet") or "")[:300], image=i.get("image"))
            for i in items if isinstance(i, dict) and i.get("url")]

def judge(cl, t, cands, S):
    res = {}
    for k in range(0, len(cands), 25):
        items = [dict(i=k + j, titre=c["title"], source=c["source"], date=c["published"].strftime("%Y-%m-%d"), extrait=c["snippet"])
                 for j, c in enumerate(cands[k:k + 25])]
        prompt = (f"Tu filtres une veille pour une collectivité. Sujet : {t['label']}.\nCritères : {t['brief']}\n"
                  "Note chaque élément de 0 à 10 : 9-10 concerne directement le sujet, 6-8 utile mais partiel, 0-5 hors sujet, "
                  "homonyme, générique ou publicitaire. Sois sévère. Résumé en français, 200 caractères max, uniquement d'après les infos fournies.\n"
                  f"Catégories possibles : {CATS}.\nÉléments : {json.dumps(items, ensure_ascii=False)}\n"
                  "Réponds uniquement par un tableau JSON [{\"i\":n,\"score\":n,\"resume\":\"...\",\"categorie\":\"...\"}].")
        r = cl.messages.create(model=S["model_filter"], max_tokens=4000, messages=[{"role": "user", "content": prompt}])
        for o in jlist(r.content[0].text):
            if isinstance(o, dict) and isinstance(o.get("i"), int): res[o["i"]] = o
    return res

def og_image(url):
    try:
        r = requests.get(url, headers=UA, timeout=8)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""): return None
        s = BeautifulSoup(r.text[:300000], "html.parser")
        for sel in ('meta[property="og:image"]', 'meta[name="twitter:image"]', 'meta[property="og:image:url"]'):
            m = s.select_one(sel)
            if m and m.get("content"): return urljoin(url, m["content"])
    except Exception: pass
    return None

def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    S = cfg["settings"]
    cl = anthropic.Anthropic()
    store = json.loads(OUT.read_text(encoding="utf-8")).get("articles", [])
    seen = json.loads(SEEN.read_text(encoding="utf-8"))
    known = {(a["topic"], tkey(a["title"])) for a in store}
    oldest = NOW - timedelta(days=S["max_age_days"])
    for t in cfg["topics"]:
        try:
            log("•", t["label"])
            c = collect_rss(t, S["max_age_days"])
            if S["discovery_max_searches"] > 0: c += discover(cl, t, S, S["max_age_days"])
            fresh, keys = [], set()
            for a in c:
                k = (t["id"], tkey(a["title"]))
                if not a["url"] or a["published"] < oldest or uid(a["url"]) in seen or k in known or k in keys: continue
                keys.add(k); fresh.append(a)
            fresh = fresh[:60]
            if not fresh: continue
            verdict = judge(cl, t, fresh, S)
            kept = []
            for j, a in enumerate(fresh):
                seen[uid(a["url"])] = NOW.isoformat()
                v = verdict.get(j)
                if v and v.get("score", 0) >= S["min_score"]: kept.append((v["score"], a, v))
            kept.sort(key=lambda x: (-x[0], -x[1]["published"].timestamp()))
            for score, a, v in kept[: 4 if t["priority"] == 2 else 12]:
                img = a["image"] or (og_image(a["url"]) if "news.google.com" not in a["url"] else None)
                store.append(dict(id=uid(a["url"]), topic=t["id"], label=t["label"], group=t["group"], priority=t["priority"],
                                  title=clean(a["title"]), url=a["url"], source=a["source"], published=a["published"].isoformat(),
                                  first_seen=NOW.isoformat(), summary=v.get("resume", ""), category=v.get("categorie", "Autre"),
                                  score=score, image=img))
            log(f"  {len(kept)} retenus sur {len(fresh)}")
        except Exception as ex:
            log("  ERREUR", t["id"], ex)
    cut = (NOW - timedelta(days=S["retention_days"])).isoformat()
    store = sorted([a for a in store if a["published"] >= cut], key=lambda a: a["published"], reverse=True)
    seen = {k: v for k, v in seen.items() if v >= (NOW - timedelta(days=45)).isoformat()}
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat(), "articles": store}, ensure_ascii=False, indent=1), encoding="utf-8")
    SEEN.write_text(json.dumps(seen), encoding="utf-8")
    log("Total :", len(store))

if __name__ == "__main__":
    sys.exit(main())
