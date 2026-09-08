"""合併 CA bundle 的組成與降級。

移植自 CARE-data/ca_bundle.py，兩份要一起維護——這裡的測試同時是那份契約
的紀錄：bundle 必須「包含 certifi 的全部內容，再加上釘選的中繼憑證」，不是
取而代之。
"""

import os

import certifi

from app.core import ca_bundle


def _reset_cache():
    ca_bundle._bundle_path = None


def test_bundle_contains_certifi_verbatim_plus_pinned_cert():
    """必須是「certifi ＋ 釘選」而不是「只有釘選」：憑證鏈要終止於自簽根
    憑證，單靠中繼憑證驗不過（Python 不設 X509_V_FLAG_PARTIAL_CHAIN）。"""
    _reset_cache()
    path = ca_bundle.get_ca_bundle()
    content = open(path, encoding="utf-8").read()
    certifi_content = open(certifi.where(), encoding="utf-8").read()

    assert certifi_content in content
    assert len(content) > len(certifi_content)
    assert content.count("BEGIN CERTIFICATE") > certifi_content.count(
        "BEGIN CERTIFICATE"
    )


def test_pinned_twca_intermediate_is_present():
    """www.mohw.gov.tw／www.hpa.gov.tw 只送 leaf 憑證，就靠這一張補鏈。"""
    _reset_cache()
    pinned = open(
        os.path.join(ca_bundle._PINNED_DIR, "twca_secure_ssl_ca.pem"), encoding="utf-8"
    ).read().strip()

    assert pinned in open(ca_bundle.get_ca_bundle(), encoding="utf-8").read()


def test_same_path_reused_within_process():
    _reset_cache()
    assert ca_bundle.get_ca_bundle() == ca_bundle.get_ca_bundle()


def test_falls_back_to_certifi_when_no_pinned_certs(tmp_path, monkeypatch):
    """釘選目錄不見了只該讓那些站台變成「判不出來」，不該讓行程起不來。"""
    _reset_cache()
    monkeypatch.setattr(ca_bundle, "_PINNED_DIR", str(tmp_path))

    assert ca_bundle.get_ca_bundle() == certifi.where()
    _reset_cache()
