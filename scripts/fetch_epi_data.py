#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_epi_data.py  --  deterministic-source collector for the `rare-disease-epi` skill.

Usage:
    python scripts/fetch_epi_data.py "<disease name>"
    python scripts/fetch_epi_data.py "Spinal muscular atrophy" --orphacode 83330
    python scripts/fetch_epi_data.py "Gaucher disease" --max-studies 150 --compact

What it does
------------
Hits ONLY the rare-disease epidemiology sources that have a clean, deterministic
public endpoint and merges them into a single JSON document printed to stdout:

    * Orphanet ................. ORPHAcode, prevalence/birth-prevalence string,
                                 classification level, synonyms, ICD-10/11, OMIM,
                                 GARD id, epidemiology paragraph, guideline links.
                                 (best-effort: server-rendered disease page parse)
    * PubMed E-utilities ....... MeSH-qualified epidemiology evidence map:
                                 prevalence / incidence / epidemiology / mortality
                                 counts + PMIDs; US & China affiliation filters;
                                 guideline-type filter; GeneReviews[Book] filter;
                                 esummary -> year / journal / pub-type per PMID.
    * ClinicalTrials.gov v2 .... trials by condition -> enrollment-count proxy,
                                 US / China counts, recent studies.
    * GARD (NCATS) ............. best-effort id echo (its API moved; usually null).

Design rules (match SKILL.md)
-----------------------------
  * stdlib only (urllib) -- no pip required, runs in any harness sandbox.
  * A source that fails OR finds nothing returns null; it is NEVER treated as
    "the disease does not exist". Every null is explained in `source_status`.
  * The dirty / API-less China side (CNKI/知网, 万方, 维普, 微信公众号, 患者组织白皮书,
    学会共识) is NOT scraped. The script returns null placeholders WITH an
    optimised search string + which site to check by hand. SKILL.md routes those
    through user paste/upload + human review, all defaulting to 🔴.
  * Orphanet may be blocked by a network allow-list in some harnesses. If so the
    source is null + a note to add the domain OR to web_fetch the disease page.
    A blocked source is an ENVIRONMENT issue, never "the disease does not exist".
  * Nothing here ever raises out of a single source; the process always prints
    valid JSON and exits 0 unless the CLI itself is misused.

This script gathers the *deterministic* slice only. Badges, the compare schema,
subtype splitting (carrier-frequency vs clinical prevalence), the China side and
the guideline -> primary-study trace-back all live in SKILL.md and are the
human/-web part of the workflow.
"""

import re
import sys
import json
import time
import argparse
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone

# Force UTF-8 stdout so Chinese notes never blow up on a Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

USER_AGENT = "rare-disease-epi/1.0 (rare-disease epidemiology intelligence; contact: research)"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_STUDIES = 200
PUBMED_RETMAX = 20

# NOTE: ClinicalTrials.gov v2 must be reached at the www. host. Some allow-lists
# only permit www.clinicaltrials.gov and 403/000 the bare apex.
CTGOV = "https://www.clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ORPHA_DETAIL = "https://www.orpha.net/en/disease/detail/{}"


# --------------------------------------------------------------------------- #
# Low-level HTTP helpers                                                       #
# --------------------------------------------------------------------------- #
def _http(url, timeout=DEFAULT_TIMEOUT, retries=1, accept="application/json"):
    """GET a URL. Returns (text, status). status in {'ok','no_data','error: ...'}. Never raises."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace"), "ok"
        except HTTPError as e:
            if e.code == 404:
                return None, "no_data"
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
            last_err = "error: HTTP {} {}".format(e.code, body).strip()
            if e.code >= 500 and attempt < retries:
                time.sleep(1.0)
                continue
            return None, last_err
        except (URLError, TimeoutError) as e:
            last_err = "error: {}: {}".format(type(e).__name__, e)
            if attempt < retries:
                time.sleep(1.0)
                continue
            return None, last_err
        except Exception as e:  # pragma: no cover - last resort
            return None, "error: {}: {}".format(type(e).__name__, e)
    return None, last_err or "error: unknown"


def http_json(url, timeout=DEFAULT_TIMEOUT, retries=1):
    text, st = _http(url, timeout=timeout, retries=retries)
    if st != "ok" or text is None:
        return None, st
    try:
        return json.loads(text), "ok"
    except (ValueError, json.JSONDecodeError) as e:
        return None, "error: bad JSON: {}".format(e)


def http_text(url, timeout=DEFAULT_TIMEOUT, retries=1):
    return _http(url, timeout=timeout, retries=retries, accept="text/html,*/*")


def _q(params):
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# --------------------------------------------------------------------------- #
# PubMed E-utilities  (the epidemiology evidence map)                          #
# --------------------------------------------------------------------------- #
def _esearch(term, db="pubmed", retmax=PUBMED_RETMAX):
    url = "{}/esearch.fcgi?{}".format(
        EUTILS, _q({"db": db, "term": term, "retmax": retmax, "retmode": "json"}))
    data, st = http_json(url)
    if st != "ok" or not data:
        return {"count": None, "pmids": [], "query": term, "status": st}
    res = data.get("esearchresult", {}) or {}
    try:
        count = int(res.get("count")) if res.get("count") is not None else None
    except (TypeError, ValueError):
        count = None
    return {
        "count": count,
        "pmids": res.get("idlist", []) or [],
        "query": term,
        "query_translation": res.get("querytranslation"),
        "status": "ok",
    }


def _esummary(pmids):
    """esummary for a set of PMIDs -> {pmid: {year, journal, title, pubtype}}."""
    out = {}
    pmids = [p for p in pmids if p]
    if not pmids:
        return out
    # batch (the unique union is small here, one call is fine)
    url = "{}/esummary.fcgi?{}".format(
        EUTILS, _q({"db": "pubmed", "id": ",".join(pmids[:50]), "retmode": "json"}))
    data, st = http_json(url)
    if st != "ok" or not data:
        return out
    res = data.get("result", {}) or {}
    for uid in res.get("uids", []) or []:
        r = res.get(uid, {}) or {}
        pubdate = r.get("pubdate") or ""
        ym = re.match(r"(\d{4})", pubdate)
        out[uid] = {
            "year": ym.group(1) if ym else None,
            "journal": r.get("source"),
            "title": r.get("title"),
            "pubtype": r.get("pubtype", []),
            "last_author": r.get("lastauthor"),
        }
    return out


def fetch_pubmed(name, status_sink):
    """Build a MeSH-qualified epidemiology evidence map for the disease."""
    dz = '"{}"'.format(name.replace('"', ""))

    metric_terms = {
        "prevalence":   "{} AND \"Prevalence\"[Mesh]".format(dz),
        "incidence":    "{} AND \"Incidence\"[Mesh]".format(dz),
        "epidemiology": "{} AND (\"Epidemiology\"[Mesh] OR epidemiology[Subheading])".format(dz),
        "mortality":    "{} AND (\"Mortality\"[Mesh] OR mortality[Subheading])".format(dz),
    }
    metric_searches = {}
    all_pmids = set()
    any_ok = False
    for k, term in metric_terms.items():
        r = _esearch(term)
        metric_searches[k] = r
        if r.get("status") == "ok":
            any_ok = True
        for p in r.get("pmids", []):
            all_pmids.add(p)

    epi_any = ("{} AND (\"Prevalence\"[Mesh] OR \"Incidence\"[Mesh] OR "
               "\"Epidemiology\"[Mesh] OR epidemiology[Subheading])").format(dz)
    country_searches = {}
    for label, adfield in (("United States", '"United States"[ad]'), ("China", "China[ad]")):
        r = _esearch("{} AND {}".format(epi_any, adfield))
        country_searches[label] = r
        for p in r.get("pmids", []):
            all_pmids.add(p)

    guidelines = _esearch(
        "{} AND (Guideline[ptyp] OR \"Practice Guideline\"[ptyp] OR "
        "\"Consensus Development Conference\"[ptyp])".format(dz))
    for p in guidelines.get("pmids", []):
        all_pmids.add(p)

    genereviews = _esearch("{} AND \"GeneReviews\"[Book]".format(dz))
    for p in genereviews.get("pmids", []):
        all_pmids.add(p)

    summaries = _esummary(sorted(all_pmids))

    if not any_ok and metric_searches["prevalence"].get("status", "").startswith("error"):
        status_sink["pubmed"] = metric_searches["prevalence"]["status"]
        return None
    status_sink["pubmed"] = "ok"
    return {
        "disease_term": dz,
        "mesh_translation": metric_searches["prevalence"].get("query_translation"),
        "metric_searches": metric_searches,
        "country_searches": country_searches,
        "guidelines": guidelines,
        "genereviews": genereviews,
        "study_summaries": summaries,
        "note": ("Counts are PubMed hit counts for the MeSH-qualified query; PMIDs are the top "
                 "{} per search. Year/journal/pub-type come from esummary. A PMID appearing under "
                 "'prevalence' means that metric MeSH matched it -- use it to seed the compare "
                 "table, then read the abstract for year/N/patient-type.".format(PUBMED_RETMAX)),
    }


# --------------------------------------------------------------------------- #
# Orphanet  (best-effort, server-rendered disease page parse)                 #
# --------------------------------------------------------------------------- #
def _strip_tags(s):
    return re.sub(r"<[^>]+>", " ", s or "")


def _clean(s):
    return re.sub(r"\s+", " ", _strip_tags(s)).strip()


def _label_value(html, label):
    """Grab the short value after a 'Label:' marker in the Orphanet page HTML."""
    m = re.search(re.escape(label) + r"\s*</[^>]+>\s*(.{0,80}?)(?:<(?:br|/p|/div|/li|h\d)|$)",
                  html, re.IGNORECASE | re.DOTALL)
    if not m:
        m = re.search(re.escape(label) + r"\s*(.{0,60}?)<", html, re.IGNORECASE | re.DOTALL)
    return _clean(m.group(1)) if m else None


def fetch_orphanet(name, status_sink, orphacode=None):
    """Parse the server-rendered Orphanet disease page for an ORPHAcode."""
    if not orphacode:
        status_sink["orphanet"] = ("no_orphacode: pass --orphacode N, or web_fetch "
                                   "https://www.orpha.net/en/disease and resolve the code by name")
        return None
    url = ORPHA_DETAIL.format(orphacode)
    html, st = http_text(url, timeout=40)
    if st != "ok" or not html:
        status_sink["orphanet"] = (
            "{} (Orphanet may be blocked by this environment's domain allow-list; "
            "add www.orpha.net / www.orphadata.com, OR web_fetch {} directly)".format(st, url))
        return None

    text = _clean(html)
    prevalence = _label_value(html, "Prevalence:")
    classification = _label_value(html, "Classification level:")
    inheritance = _label_value(html, "Inheritance:")
    age_onset = _label_value(html, "Age of onset:")
    icd10 = _label_value(html, "ICD-10:")
    icd11 = _label_value(html, "ICD-11:")
    omim = None
    m = re.search(r"OMIM:\s*</[^>]+>\s*<[^>]*>?\s*(\d{6})", html) or re.search(r"OMIM[^0-9]{0,40}(\d{6})", text)
    if m:
        omim = m.group(1)
    gard = None
    m = re.search(r"GARD:?[^0-9]{0,40}(\d{3,7})", text)
    if m:
        gard = m.group(1)

    # Epidemiology paragraph (between the 'Epidemiology' heading and the next heading).
    epi_para = None
    m = re.search(r"Epidemiology\s*(.{20,800}?)(?:Clinical description|Clinical features|Etiology|"
                  r"Diagnostic methods|Management)", text, re.IGNORECASE)
    if m:
        epi_para = m.group(1).strip()

    definition = None
    m = re.search(r"Disease definition\s*(.{20,600}?)(?:ORPHA:|Classification level)", text, re.IGNORECASE)
    if m:
        definition = m.group(1).strip()

    # Synonyms
    syns = []
    m = re.search(r"Synonym\(s\):(.{0,400}?)(?:Prevalence|Inheritance|Disease definition|ICD)", html, re.IGNORECASE | re.DOTALL)
    if m:
        for s in re.findall(r"<li>(.*?)</li>", m.group(1), re.DOTALL):
            v = _clean(s)
            if v:
                syns.append(v)

    # Guideline / GeneReviews links (PubMed + has-sante + orphananesthesia etc.)
    guideline_links = []
    for href, anchor in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        low = href.lower()
        if ("guideline" in low or "pubmed.ncbi" in low or "genereviews" in low
                or "/books/nbk" in low or "has-sante" in low or "orphananesthesia" in low):
            guideline_links.append({"url": href, "text": _clean(anchor)[:80]})
    # dedupe by url, keep first 15
    seen, gl = set(), []
    for g in guideline_links:
        if g["url"] in seen:
            continue
        seen.add(g["url"])
        gl.append(g)
        if len(gl) >= 15:
            break

    status_sink["orphanet"] = "ok"
    return {
        "orphacode": str(orphacode),
        "url": url,
        "classification_level": classification,
        "prevalence_orphanet": prevalence,
        "definition": definition,
        "epidemiology_text": epi_para,
        "synonyms": syns[:12],
        "inheritance": inheritance,
        "age_of_onset": age_onset,
        "icd10": icd10,
        "icd11": icd11,
        "omim": omim,
        "gard_id": gard,
        "guideline_links": gl,
        "note": ("Orphanet prevalence is a CURATED estimate (often a band like '1-9/100 000' or a "
                 "point birth-prevalence). It is secondary/consensus -- trace the cited source for "
                 "year/N/patient-type before filling the compare table. Validation status & per-"
                 "subtype codes: open the Orphanet page / Orphadata epidemiology product."),
    }


# --------------------------------------------------------------------------- #
# ClinicalTrials.gov v2  (enrollment proxy, NOT a prevalence)                  #
# --------------------------------------------------------------------------- #
def _dig(d, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def fetch_clinicaltrials(name, status_sink, max_studies=DEFAULT_MAX_STUDIES):
    studies = []
    total = None
    token = None
    while len(studies) < max_studies:
        params = {"query.cond": name, "pageSize": 100, "countTotal": "true"}
        if token:
            params["pageToken"] = token
        url = "{}?{}".format(CTGOV, _q(params))
        data, st = http_json(url, timeout=45)
        if st != "ok" or not data:
            if not studies:
                status_sink["clinicaltrials_gov"] = "no_data" if st == "no_data" else st
                return None
            break
        if total is None:
            total = data.get("totalCount")
        studies.extend(data.get("studies", []) or [])
        token = data.get("nextPageToken")
        if not token:
            break
    if not studies:
        status_sink["clinicaltrials_gov"] = "no_data"
        return None
    studies = studies[:max_studies]

    parsed = []
    for s in studies:
        ps = s.get("protocolSection", {}) or {}
        enr = _dig(ps, "designModule", "enrollmentInfo") or {}
        countries = sorted({l.get("country") for l in
                            (_dig(ps, "contactsLocationsModule", "locations") or [])
                            if l.get("country")})
        parsed.append({
            "nctId": _dig(ps, "identificationModule", "nctId"),
            "briefTitle": _dig(ps, "identificationModule", "briefTitle"),
            "overallStatus": _dig(ps, "statusModule", "overallStatus"),
            "startDate": _dig(ps, "statusModule", "startDateStruct", "date"),
            "enrollment_count": enr.get("count"),
            "enrollment_type": enr.get("type"),
            "countries": countries,
        })

    def country_count(tag):
        return sum(1 for p in parsed if tag in p["countries"])

    actual = [p["enrollment_count"] for p in parsed
              if p.get("enrollment_type") == "ACTUAL" and isinstance(p.get("enrollment_count"), int)]
    recent = sorted([p for p in parsed if p["startDate"]],
                    key=lambda p: p["startDate"], reverse=True)[:8]

    status_sink["clinicaltrials_gov"] = "ok"
    return {
        "total_matching": total,
        "fetched": len(parsed),
        "enrollment_proxy": {
            "note": ("Enrollment is a ROUGH activity proxy, NOT a prevalence/incidence. "
                     "It hints at how many patients trials could recruit -- nothing more."),
            "actual_enrollment_studies": len(actual),
            "actual_enrollment_sum": sum(actual) if actual else 0,
            "actual_enrollment_max": max(actual) if actual else None,
        },
        "us_trial_count": country_count("United States"),
        "china_trial_count": country_count("China"),
        "recent_trials": recent,
        "studies": parsed,
    }


# --------------------------------------------------------------------------- #
# GARD  (NCATS) -- best effort; its public API moved, usually null            #
# --------------------------------------------------------------------------- #
def fetch_gard(name, status_sink, gard_id=None):
    if not gard_id:
        status_sink["gard"] = ("no_gard_id: GARD has no stable open API; the GARD id (if any) is "
                               "echoed by Orphanet. Check rarediseases.info.nih.gov by hand.")
        return None
    url = "https://rarediseases.info.nih.gov/diseases/{}/index".format(gard_id)
    text, st = http_text(url, timeout=25)
    if st != "ok" or not text:
        status_sink["gard"] = "{} (GARD page; may be blocked or client-rendered -> web_fetch by hand)".format(st)
        return None
    status_sink["gard"] = "ok"
    return {"gard_id": str(gard_id), "url": url,
            "note": "GARD is US-side supplementary; it rarely carries hard epi numbers. Verify by hand."}


# --------------------------------------------------------------------------- #
# API-less China side: null placeholders WITH optimised search strings        #
# --------------------------------------------------------------------------- #
def china_manual_placeholders(name):
    n = urllib.parse.quote(name)
    return {
        "_about": ("China rare-disease epi has NO clean open API (CNKI/Wanfang/VIP are paywalled & "
                   "anti-scraped; WeChat 公众号 is not programmatically accessible; patient-org "
                   "白皮书 are scattered PDFs). The script does NOT scrape these. It hands you an "
                   "optimised query per source; you paste/upload the原文, Claude normalises it into "
                   "the compare schema. EVERY China conclusion defaults to 🔴 (the China gate)."),
        "cnki": {
            "data": None,
            "site": "https://kns.cnki.net/  (知网高级检索)",
            "recommended_query": "主题=({0} OR {0}患病率 OR {0}发病率 OR {0}流行病学) 并含 (中国 OR 全国 OR 省)".format(name),
            "note": "知网高级检索：主题/篇关摘 限定病名 + 患病率/发病率/流行病学 + 地域。付费墙，需手动下载原文。",
        },
        "wanfang": {
            "data": None,
            "site": "https://www.wanfangdata.com.cn/",
            "recommended_query": "{} AND (患病率 OR 发病率 OR 流行病学 OR 登记)".format(name),
            "note": "万方医学：补充 CNKI 漏检的中文期刊与会议文献。手动下载。",
        },
        "vip": {
            "data": None,
            "site": "http://qikan.cqvip.com/  (维普)",
            "recommended_query": "{} AND (患病率 OR 发病率 OR 流行病学)".format(name),
            "note": "维普中文期刊：第三来源交叉核对。手动。",
        },
        "wechat_official": {
            "data": None,
            "site": "微信搜一搜 / 搜狗微信 https://weixin.sogou.com/",
            "recommended_query": "{} 患病率 中国 / {} 流行病学 白皮书".format(name, name),
            "note": "公众号（患者组织、专病平台）常首发中国流调与白皮书，但基本无法编程访问。人工搜+截图/粘贴。",
        },
        "patient_org_whitepaper": {
            "data": None,
            "site": "病痛挑战基金会 / 蔻德罕见病中心(CORD) / 专病患者组织官网",
            "recommended_query": "{} 中国 白皮书 / 患者生存状况调研报告".format(name),
            "note": "白皮书散落为 PDF。即便拿到官方白皮书，中国侧仍默认 🔴（口径与样本常不透明）。",
        },
        "nhc_official_guidelines": {
            "data": None,
            "site": "http://www.nhc.gov.cn/  (国家卫健委)",
            "recommended_query": "罕见病诊疗指南 2019 / 86个罕见病病种诊疗指南 2025 -> 查 '{}' 是否在 207 个目录病种内".format(name),
            "note": ("最高等级官方文件。两批目录共 207 病种（2019 版 121 + 2025 版 86）。"
                     "📘 可叠 🏛️官方，但其 epi 数字仍是二手→须溯原始研究。目录外病种无官方指南→转学会共识。"),
        },
        "society_consensus": {
            "data": None,
            "site": "医脉通 https://guide.medlive.cn/  ｜ 梅斯 https://www.medsci.cn/guideline",
            "recommended_query": "中国 {} 诊治指南 / 专家共识".format(name),
            "note": "中华医学会各专科共识。📘 高等级，用于病例定义/诊断标准与亚型分类；其 epi 数字仍须溯原始研究。",
        },
    }


def web_and_manual_placeholders(name):
    return {
        "gbd_ihme": {
            "data": None,
            "site": "https://vizhub.healthdata.org/gbd-results/  (GBD Results Tool)",
            "note": ("Global Burden of Disease 全球补充。⚠️ GBD 对单个罕见病覆盖弱（多并入大类，"
                     "细到具体罕见病常缺失）。仅作量级旁证，不作主源。"),
        },
        "orphanet_manual": {
            "data": None,
            "site": "https://www.orpha.net/en/disease  ｜  https://www.orphadata.com/",
            "note": ("若脚本 Orphanet 源为 null（多因域名未放行）：直接 web_fetch "
                     "https://www.orpha.net/en/disease/detail/<ORPHAcode> 取 Prevalence + Epidemiology + "
                     "validation status + 各亚型 ORPHAcode；或下载 Orphadata 'epidemiology (prevalence)' 数据集。"),
        },
        "japan_nanbyo_v2": {
            "data": None,
            "site": "https://www.nanbyou.or.jp/  ｜  https://www.mhlw.go.jp/  (指定難病 受給者証)",
            "note": "v2 TODO：日本「指定難病」受給者証所持者数 年度登记（需日文页面 parser）。见 SKILL.md v2 stub。",
        },
        "korea_kdca_v2": {
            "data": None,
            "site": "https://www.kdca.go.kr/  (희귀질환자 통계 / 산정특례)",
            "note": "v2 TODO：韩国 KDCA 희귀질환 산정특례 登记统计（需韩文页面 parser）。见 SKILL.md v2 stub。",
        },
    }


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def _safe(fn, name, status, key, extra=()):
    try:
        return fn(name, status, *extra)
    except Exception as e:
        status[key] = "error: unhandled {}: {}".format(type(e).__name__, e)
        return None


def collect(name, orphacode=None, gard_id=None, max_studies=DEFAULT_MAX_STUDIES):
    status = {}
    result = {
        "query": name,
        "orphacode": str(orphacode) if orphacode else None,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "1.0",
        "regions_scope": ["International (Orphanet/PubMed)", "United States", "China"],
        "deterministic_sources": {},
        "china_manual": china_manual_placeholders(name),
        "web_and_manual": web_and_manual_placeholders(name),
        "source_status": status,
        "disclaimer": ("Rare-disease epidemiology intelligence only. Not medical advice. Every epi "
                       "number must carry a badge + collection year + sample size per SKILL.md. "
                       "Prefer ❓N/A over a fabricated number. A null/blocked source NEVER means the "
                       "disease does not exist."),
    }
    ds = result["deterministic_sources"]
    ds["orphanet"] = _safe(fetch_orphanet, name, status, key="orphanet", extra=(orphacode,))
    ds["pubmed"] = _safe(fetch_pubmed, name, status, key="pubmed")
    ds["clinicaltrials_gov"] = _safe(fetch_clinicaltrials, name, status,
                                     key="clinicaltrials_gov", extra=(max_studies,))
    # GARD id can come from the Orphanet result if the user did not pass one.
    if not gard_id and ds.get("orphanet"):
        gard_id = ds["orphanet"].get("gard_id")
    ds["gard"] = _safe(fetch_gard, name, status, key="gard", extra=(gard_id,))

    result["identity_rollup"] = build_identity(name, orphacode, ds)
    return result


def build_identity(name, orphacode, ds):
    ident = {"query": name, "orphacode": str(orphacode) if orphacode else None,
             "omim": None, "icd10": None, "icd11": None, "gard_id": None,
             "classification_level": None, "synonyms": [], "mesh_translation": None,
             "subtype_hint": None}
    orph = ds.get("orphanet")
    if orph:
        ident["orphacode"] = orph.get("orphacode")
        ident["omim"] = orph.get("omim")
        ident["icd10"] = orph.get("icd10")
        ident["icd11"] = orph.get("icd11")
        ident["gard_id"] = orph.get("gard_id")
        ident["classification_level"] = orph.get("classification_level")
        ident["synonyms"] = orph.get("synonyms", [])[:8]
        cl = (orph.get("classification_level") or "").lower()
        if "subtype" in cl:
            ident["subtype_hint"] = ("This ORPHAcode is itself a SUBTYPE entry -> check the parent "
                                     "disorder and sibling subtypes; split epi per subtype (G2).")
        elif "group" in cl or "disorder" in cl:
            ident["subtype_hint"] = ("This ORPHAcode is a disorder/group -> check whether Orphanet "
                                     "lists treatment-distinct subtypes to split (G2).")
    pm = ds.get("pubmed")
    if pm:
        ident["mesh_translation"] = pm.get("mesh_translation")
    return ident


def main(argv):
    ap = argparse.ArgumentParser(
        description="Collect deterministic rare-disease epi from Orphanet, PubMed, ClinicalTrials.gov, GARD.")
    ap.add_argument("name", help="disease name (quote it). PubMed auto-maps it to a MeSH term.")
    ap.add_argument("--orphacode", type=int, default=None,
                    help="Orphanet ORPHAcode for precise epidemiology lookup (recommended).")
    ap.add_argument("--gard-id", type=int, default=None, help="GARD id (else taken from Orphanet).")
    ap.add_argument("--max-studies", type=int, default=DEFAULT_MAX_STUDIES,
                    help="cap of ClinicalTrials.gov studies to pull (default %(default)s)")
    ap.add_argument("--no-studies-list", action="store_true",
                    help="drop the per-study CT.gov array (keep computed summaries)")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    args = ap.parse_args(argv)

    result = collect(args.name, orphacode=args.orphacode, gard_id=args.gard_id,
                     max_studies=args.max_studies)

    if args.no_studies_list and result["deterministic_sources"].get("clinicaltrials_gov"):
        result["deterministic_sources"]["clinicaltrials_gov"].pop("studies", None)

    if args.compact:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
