"""보고자 법인 → 상장 모회사 그룹 구조 역추적.

알고리즘:
  1) 보고자 명칭이 *상장사 매핑* 에 직접 있으면 → 그 corp_code 사용 (쉬운 path).
  2) 비상장 보고자의 경우:
     - corp_code_map 의 corp_name 에 *어근 매칭* 으로 후보 상장사 추출
       (예: "모트렉스이에프엠" → 어근 "모트렉스" → 후보 [모트렉스])
     - 후보 상장사의 사업보고서 otrCprInvstmntSttus 조회 → 자회사 리스트에서
       보고자 명칭과 정확 매칭 시도
     - 발견 시 → 모회사 = 그 상장사
  3) 두 단계 모두 실패 → "비상장 비추적" + 사람 검증 폴백

⚠️ 한계: 완전한 그룹 구조 추적은 DART 전체 corpCode (~118만개) 가 필요.
이 모듈은 *상장 모회사가 1~2 hop 안에 있는* 그룹 (전형적인 KOSPI 그룹 구조)
케이스를 우선 처리한다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from activist_scout.config import CORP_MAP_FILE
from activist_scout.utils import dart_get


_NORMALIZE_RE = re.compile(
    r"(주식회사|㈜|\(주\)|Co\.,?\s*Ltd\.?|Inc\.?|Corp\.?|Limited|LLC|S\.A\.|GmbH)",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    if not name:
        return ""
    n = _NORMALIZE_RE.sub("", name)
    n = re.sub(r"[\s​]+", "", n)  # whitespace + zero-width
    return n.strip()


def _load_corp_map() -> dict[str, dict]:
    with open(CORP_MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def find_listed_by_name(name: str, corp_map: dict[str, dict] | None = None) -> list[dict]:
    """상장사 매핑에서 정규화된 이름이 일치하는 corp_code 후보 반환."""
    norm_target = _normalize(name)
    if not norm_target:
        return []
    cm = corp_map or _load_corp_map()
    hits = []
    for stock_code, info in cm.items():
        norm_listed = _normalize(info.get("corp_name", ""))
        if not norm_listed:
            continue
        if norm_listed == norm_target:
            hits.append({"stock_code": stock_code, **info, "match": "exact"})
        elif norm_target.startswith(norm_listed) and len(norm_listed) >= 2:
            # 보고자 명이 상장사 명으로 시작 (예: "모트렉스이에프엠" startswith "모트렉스")
            hits.append({"stock_code": stock_code, **info, "match": "prefix"})
    return hits


def fetch_subsidiaries(corp_code: str, bsns_year: str = "2024") -> list[dict[str, Any]]:
    """상장사의 사업보고서 §VIII 타법인 출자 (자회사 리스트)."""
    data = dart_get(
        "otrCprInvstmntSttus.json",
        {"corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": "11011"},
    )
    if not data or data.get("status") != "000":
        return []
    return list(data.get("list", []))


def find_parent_via_subsidiaries(
    filer_name: str, corp_map: dict[str, dict] | None = None
) -> dict[str, Any] | None:
    """비상장 보고자의 *상장 모회사* 를 자회사 리스트 역참조로 찾는다.

    어근 매칭으로 후보 상장사 5개 이내로 좁힌 뒤, 각 상장사의 자회사 리스트에서
    보고자 명칭과 정규화 일치하는 자회사를 찾는다.
    """
    norm_filer = _normalize(filer_name)
    if not norm_filer or len(norm_filer) < 3:
        return None

    cm = corp_map or _load_corp_map()
    # 어근 후보: 보고자 명의 앞 3~6글자가 상장사 명에 들어 있는 경우
    candidates = []
    for stock_code, info in cm.items():
        norm_listed = _normalize(info.get("corp_name", ""))
        if not norm_listed or len(norm_listed) < 2:
            continue
        # 보고자가 상장사 명을 prefix 로 가지면 강한 후보
        if norm_filer.startswith(norm_listed):
            candidates.append({"stock_code": stock_code, **info, "priority": 0})
        # 또는 두 이름이 공통 어근 공유 (앞 3글자 일치)
        elif norm_listed[:3] == norm_filer[:3]:
            candidates.append({"stock_code": stock_code, **info, "priority": 1})

    candidates.sort(key=lambda x: x["priority"])
    for cand in candidates[:5]:  # 상위 5개만 검증 (DART rate-limit 고려)
        subs = fetch_subsidiaries(cand["corp_code"])
        for sub in subs:
            inv_prm = sub.get("inv_prm", "") or ""
            if _normalize(inv_prm) == norm_filer:
                return {
                    "filer_name": filer_name,
                    "filer_is_listed": False,
                    "parent_stock_code": cand["stock_code"],
                    "parent_corp_code": cand["corp_code"],
                    "parent_corp_name": cand["corp_name"],
                    "match_method": "subsidiary_reverse_lookup",
                    "evidence_inv_prm": inv_prm,
                }
    return None


def resolve(filer_name: str) -> dict[str, Any]:
    """보고자 명칭 → 그룹 구조 매핑.

    반환:
      {
        "filer_name": str,
        "filer_is_listed": bool,
        "parent_stock_code": str | None,
        "parent_corp_code": str | None,
        "parent_corp_name": str | None,
        "match_method": "direct_listed" | "subsidiary_reverse_lookup" | "unresolved",
        "siblings": [str, ...]   # 같은 모회사 산하 다른 자회사 (시너지 분석용)
      }
    """
    cm = _load_corp_map()

    # 1) 상장사 직접 매칭
    direct = find_listed_by_name(filer_name, cm)
    exact = [h for h in direct if h["match"] == "exact"]
    if exact:
        return {
            "filer_name": filer_name,
            "filer_is_listed": True,
            "parent_stock_code": exact[0]["stock_code"],
            "parent_corp_code": exact[0]["corp_code"],
            "parent_corp_name": exact[0]["corp_name"],
            "match_method": "direct_listed",
            "siblings": [],
        }

    # 2) 비상장 → 자회사 역참조
    via_sub = find_parent_via_subsidiaries(filer_name, cm)
    if via_sub:
        # 같은 모회사 산하 다른 자회사 = 시너지 후보
        subs = fetch_subsidiaries(via_sub["parent_corp_code"])
        siblings = [
            s.get("inv_prm", "")
            for s in subs
            if _normalize(s.get("inv_prm", "")) != _normalize(filer_name)
        ]
        via_sub["siblings"] = [s for s in siblings if s]
        return via_sub

    # 3) 추적 실패
    return {
        "filer_name": filer_name,
        "filer_is_listed": False,
        "parent_stock_code": None,
        "parent_corp_code": None,
        "parent_corp_name": None,
        "match_method": "unresolved",
        "siblings": [],
    }
